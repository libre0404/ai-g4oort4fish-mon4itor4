"""
闲鱼爬虫核心执行模块
优化版本 - 集成 DelayConfig、UserAgentManager、IPBlockerDetector
适配内容创作者的二手设备采购场景
"""

import asyncio
import json
import os
import random
from datetime import datetime
from urllib.parse import urlencode

from playwright.async_api import (
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from .stealth_helper import StealthManager

from src.ai_handler import (
    download_all_images,
    get_ai_analysis,
    send_ntfy_notification,
    cleanup_task_images,
)
from src.config import (
    AI_DEBUG_MODE,
    API_URL_PATTERN,
    DETAIL_API_URL_PATTERN,
    LOGIN_IS_EDGE,
    RUN_HEADLESS,
    RUNNING_IN_DOCKER,
    STATE_FILE,
    SKIP_AI_ANALYSIS,
)
from src.parsers import (
    _parse_search_results_json,
    _parse_user_items_data,
    calculate_reputation_from_ratings,
    parse_ratings_data,
    parse_user_head_data,
)
from src.utils import (
    format_registration_days,
    get_link_unique_key,
    random_sleep,
    safe_get,
    save_to_jsonl,
    log_time,
)
from src.optimization import DelayConfig, UserAgentManager, IPBlockerDetector
from src.config import get_random_user_agent


async def scrape_user_profile(context, user_id: str) -> dict:
    print(f"   -> 开始采集用户ID: {user_id} 的完整信息...")
    profile_data = {}

    page = await context.new_page()
    await StealthManager.apply_stealth_async(page)

    head_api_future = asyncio.get_event_loop().create_future()
    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        if "mtop.idle.web.user.page.head" in response.url and not head_api_future.done():
            try:
                head_api_future.set_result(await response.json())
                print("      [API捕获] 用户头部信息... 成功")
            except Exception:
                if not head_api_future.done():
                    head_api_future.set_exception(RuntimeError("头部信息解析失败"))
        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 商品列表... 当前已捕获 {len(all_items)} 件")
                if not data.get("data", {}).get("nextPage", True):
                    stop_item_scrolling.set()
            except Exception:
                stop_item_scrolling.set()
        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get("data", {}).get("cardList", []))
                print(f"      [API捕获] 评价列表... 当前已捕获 {len(all_ratings)} 条")
                if not data.get("data", {}).get("nextPage", True):
                    stop_rating_scrolling.set()
            except Exception:
                stop_rating_scrolling.set()

    page.on("response", handle_response)

    try:
        await page.goto(
            f"https://www.goofish.com/personal?userId={user_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        head_data = await asyncio.wait_for(head_api_future, timeout=15)
        profile_data = await parse_user_head_data(head_data)

        print("      [采集阶段] 开始采集该用户的商品列表...")
        await DelayConfig.smart_delay("api_wait")
        while not stop_item_scrolling.is_set():
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            try:
                await asyncio.wait_for(stop_item_scrolling.wait(), timeout=8)
            except asyncio.TimeoutError:
                print("      [滚动超时] 商品列表可能已加载完毕。")
                break
        profile_data["卖家发布的商品列表"] = await _parse_user_items_data(all_items)

        print("      [采集阶段] 开始采集该用户的评价列表...")
        rating_tab_locator = page.locator("//div[text()='信用及评价']/ancestor::li")
        if await rating_tab_locator.count() > 0:
            await rating_tab_locator.click()
            await DelayConfig.smart_delay("api_wait")

            while not stop_rating_scrolling.is_set():
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                try:
                    await asyncio.wait_for(stop_rating_scrolling.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("      [滚动超时] 评价列表可能已加载完毕。")
                    break

            profile_data["卖家收到的评价列表"] = await parse_ratings_data(all_ratings)
            reputation_stats = await calculate_reputation_from_ratings(all_ratings)
            profile_data.update(reputation_stats)
        else:
            print("      [警告] 未找到评价选项卡，跳过评价采集。")

    except Exception as e:
        print(f"   [错误] 采集用户 {user_id} 信息时发生错误: {e}")
    finally:
        page.remove_listener("response", handle_response)
        await page.close()
        print(f"   -> 用户 {user_id} 信息采集完成。")

    return profile_data


async def scrape_xianyu(task_config: dict, debug_limit: int = 0):
    """
    【核心执行器】
    根据单个任务配置，异步爬取闲鱼商品数据，并对每个新发现的商品进行实时的、独立的AI分析和通知。
    """
    keyword = task_config["keyword"]
    max_pages = task_config.get("max_pages", 1)
    personal_only = task_config.get("personal_only", False)
    min_price = task_config.get("min_price")
    max_price = task_config.get("max_price")
    ai_prompt_text = task_config.get("ai_prompt_text", "")

    processed_item_count = 0
    stop_scraping = False

    ip_detector = IPBlockerDetector(max_consecutive_fails=3)

    processed_links = set()
    output_filename = os.path.join("jsonl", f"{keyword.replace(' ', '_')}_full_data.jsonl")
    if os.path.exists(output_filename):
        print(f"LOG: 发现已存在文件 {output_filename}，正在加载历史记录以去重...")
        try:
            with open(output_filename, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        link = record.get("商品信息", {}).get("商品链接", "")
                        if link:
                            processed_links.add(get_link_unique_key(link))
                    except json.JSONDecodeError:
                        print("   [警告] 文件中有一行无法解析为JSON，已跳过。")
            print(f"LOG: 加载完成，已记录 {len(processed_links)} 个已处理过的商品。")
        except IOError as e:
            print(f"   [警告] 读取历史文件时发生错误: {e}")
    else:
        print(f"LOG: 输出文件 {output_filename} 不存在，将创建新文件。")

    async with async_playwright() as p:
        launch_config = StealthManager.get_launch_config(headless=RUN_HEADLESS)

        if LOGIN_IS_EDGE:
            browser = await p.chromium.launch(
                channel="msedge",
                **launch_config,
            )
        else:
            if RUNNING_IN_DOCKER:
                browser = await p.chromium.launch(
                    **launch_config,
                )
            else:
                browser = await p.chromium.launch(
                    channel="chrome",
                    **launch_config,
                )

        random_ua = get_random_user_agent()
        print(f"🔄 使用User-Agent: {random_ua[:80]}...")

        context = await browser.new_context(
            storage_state=STATE_FILE,
            user_agent=random_ua,
            **StealthManager.get_context_config(),
        )

        page = await context.new_page()
        await StealthManager.apply_stealth_async(page)

        try:
            log_time("步骤 1 - 直接导航到搜索结果页...")
            params = {"q": keyword}
            search_url = f"https://www.goofish.com/search?{urlencode(params)}"
            log_time(f"目标URL: {search_url}")

            async with page.expect_response(
                lambda r: API_URL_PATTERN in r.url, timeout=30000
            ) as response_info:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

            initial_response = await response_info.value

            await page.wait_for_selector("text=新发布", timeout=15000)

            baxia_dialog = page.locator("div.baxia-dialog-mask")
            middleware_widget = page.locator("div.J_MIDDLEWARE_FRAME_WIDGET")
            is_blocked = False

            try:
                await baxia_dialog.wait_for(state="visible", timeout=2000)
                is_blocked = True
                print("\n" + "=" * 60)
                print("【反爬虫检测】检测到 baxia-dialog 验证弹窗")
                print("=" * 60)
                await ip_detector.handle_blocked()
                print("建议：")
                print("1. 停止脚本一段时间再试")
                print("2. 设置 RUN_HEADLESS=false 使用非无头模式")
                print("3. 检查代理设置或更换IP")
                print(f"任务 '{keyword}' 将在此处中止")
                print("=" * 60 + "\n")
            except PlaywrightTimeoutError:
                pass

            if not is_blocked:
                try:
                    await middleware_widget.wait_for(state="visible", timeout=2000)
                    is_blocked = True
                    print("\n" + "=" * 60)
                    print("【反爬虫检测】检测到 J_MIDDLEWARE_FRAME_WIDGET 验证弹窗")
                    print("=" * 60)
                    await ip_detector.handle_blocked()
                    print("建议：")
                    print("1. 停止脚本一段时间再试")
                    print("2. 更新登录状态文件")
                    print("3. 降低任务执行频率")
                    print(f"任务 '{keyword}' 将在此处中止")
                    print("=" * 60 + "\n")
                except PlaywrightTimeoutError:
                    ip_detector.reset_fails()

            if is_blocked:
                await browser.close()
                return processed_item_count

            try:
                await page.click("div[class*='closeIconBg']", timeout=3000)
                print("LOG: 已关闭广告弹窗。")
            except PlaywrightTimeoutError:
                print("LOG: 未检测到广告弹窗。")

            final_response = None
            log_time("步骤 2 - 应用筛选条件...")
            await page.click("text=新发布")
            await random_sleep(2, 4)
            async with page.expect_response(
                lambda r: API_URL_PATTERN in r.url, timeout=20000
            ) as response_info:
                await page.click("text=最新")
                await random_sleep(4, 7)
            final_response = await response_info.value

            if personal_only:
                async with page.expect_response(
                    lambda r: API_URL_PATTERN in r.url, timeout=20000
                ) as response_info:
                    await page.click("text=个人闲置")
                    await random_sleep(4, 6)
                final_response = await response_info.value

            if min_price or max_price:
                price_container = page.locator(
                    'div[class*="search-price-input-container"]'
                ).first
                if await price_container.is_visible():
                    if min_price:
                        await price_container.get_by_placeholder("¥").first.fill(
                            min_price
                        )
                        await random_sleep(1, 2.5)
                    if max_price:
                        await price_container.get_by_placeholder("¥").nth(1).fill(
                            max_price
                        )
                        await random_sleep(1, 2.5)

                    async with page.expect_response(
                        lambda r: API_URL_PATTERN in r.url, timeout=20000
                    ) as response_info:
                        await page.keyboard.press("Tab")
                        await random_sleep(4, 7)
                    final_response = await response_info.value
                else:
                    print("LOG: 警告 - 未找到价格输入容器。")

            log_time("所有筛选已完成，开始处理商品列表...")

            current_response = (
                final_response if final_response and final_response.ok else initial_response
            )

            for page_num in range(1, max_pages + 1):
                if stop_scraping:
                    break
                log_time(f"开始处理第 {page_num}/{max_pages} 页 ...")

                if page_num > 1:
                    next_btn = page.locator(
                        "[class*='search-pagination-arrow-right']:not([class*='disabled'])"
                    )
                    if not await next_btn.count():
                        log_time("已到达最后一页，未找到可用的‘下一页’按钮，停止翻页。")
                        break
                    try:
                        async with page.expect_response(
                            lambda r: API_URL_PATTERN in r.url, timeout=20000
                        ) as response_info:
                            await next_btn.click()
                            await random_sleep(5, 8)
                        current_response = await response_info.value
                    except PlaywrightTimeoutError:
                        log_time(f"翻页到第 {page_num} 页超时，停止翻页。")
                        break

                if not (current_response and current_response.ok):
                    log_time(f"第 {page_num} 页响应无效，跳过。")
                    continue

                basic_items = await _parse_search_results_json(
                    await current_response.json(), f"第 {page_num} 页"
                )
                if not basic_items:
                    break

                total_items_on_page = len(basic_items)
                for i, item_data in enumerate(basic_items, 1):
                    if debug_limit > 0 and processed_item_count >= debug_limit:
                        log_time(f"已达到调试上限 ({debug_limit})，停止获取新商品。")
                        stop_scraping = True
                        break

                    unique_key = get_link_unique_key(item_data["商品链接"])
                    if unique_key in processed_links:
                        log_time(
                            f"[页内进度 {i}/{total_items_on_page}] 商品 '{item_data['商品标题'][:20]}...' 已存在，跳过。"
                        )
                        continue

                    log_time(
                        f"[页内进度 {i}/{total_items_on_page}] 发现新商品，获取详情: {item_data['商品标题'][:30]}..."
                    )
                    await random_sleep(3, 6)

                    detail_page = await context.new_page()
                    await StealthManager.apply_stealth_async(detail_page)
                    try:
                        async with detail_page.expect_response(
                            lambda r: DETAIL_API_URL_PATTERN in r.url, timeout=25000
                        ) as detail_info:
                            await detail_page.goto(
                                item_data["商品链接"],
                                wait_until="domcontentloaded",
                                timeout=25000,
                            )

                        detail_response = await detail_info.value
                        if detail_response.ok:
                            detail_json = await detail_response.json()

                            ret_string = str(
                                await safe_get(detail_json, "ret", default=[])
                            )
                            if "FAIL_SYS_USER_VALIDATE" in ret_string:
                                print(
                                    "\n==================== CRITICAL BLOCK DETECTED ===================="
                                )
                                print(
                                    "检测到闲鱼反爬虫验证 (FAIL_SYS_USER_VALIDATE)，程序将终止。"
                                )
                                long_sleep_duration = random.randint(3, 60)
                                print(
                                    f"为避免账户风险，将执行一次长时间休眠 ({long_sleep_duration} 秒) 后再退出..."
                                )
                                await asyncio.sleep(long_sleep_duration)
                                print("长时间休眠结束，现在将安全退出。")
                                print(
                                    "==================================================================="
                                )
                                stop_scraping = True
                                break

                            item_do = await safe_get(
                                detail_json, "data", "itemDO", default={}
                            )
                            seller_do = await safe_get(
                                detail_json, "data", "sellerDO", default={}
                            )

                            reg_days_raw = await safe_get(
                                seller_do, "userRegDay", default=0
                            )
                            registration_duration_text = format_registration_days(
                                reg_days_raw
                            )

                            zhima_credit_text = await safe_get(
                                seller_do, "zhimaLevelInfo", "levelName"
                            )

                            image_infos = await safe_get(
                                item_do, "imageInfos", default=[]
                            )
                            if image_infos:
                                all_image_urls = [
                                    img.get("url")
                                    for img in image_infos
                                    if img.get("url")
                                ]
                                if all_image_urls:
                                    item_data["商品图片列表"] = all_image_urls
                                    item_data["商品主图链接"] = all_image_urls[0]

                            item_data["“想要”人数"] = await safe_get(
                                item_do,
                                "wantCnt",
                                default=item_data.get("“想要”人数", "NaN"),
                            )
                            item_data["浏览量"] = await safe_get(
                                item_do, "browseCnt", default="-"
                            )

                            user_profile_data = {}
                            user_id = await safe_get(seller_do, "sellerId")
                            if user_id:
                                user_profile_data = await scrape_user_profile(
                                    context, str(user_id)
                                )
                            else:
                                print("   [警告] 未能从详情API中获取到卖家ID。")

                            user_profile_data["卖家芝麻信用"] = zhima_credit_text
                            user_profile_data["卖家注册时长"] = registration_duration_text

                            final_record = {
                                "爬取时间": datetime.now().isoformat(),
                                "搜索关键字": keyword,
                                "任务名称": task_config.get(
                                    "task_name", "Untitled Task"
                                ),
                                "商品信息": item_data,
                                "卖家信息": user_profile_data,
                            }

                            if SKIP_AI_ANALYSIS:
                                log_time(
                                    "环境变量 SKIP_AI_ANALYSIS 已设置，跳过AI分析并直接发送通知..."
                                )
                                image_urls = item_data.get("商品图片列表", [])
                                downloaded_image_paths = await download_all_images(
                                    item_data["商品ID"],
                                    image_urls,
                                    task_config.get("task_name", "default"),
                                )

                                for img_path in downloaded_image_paths:
                                    try:
                                        if os.path.exists(img_path):
                                            os.remove(img_path)
                                            print(
                                                f"   [图片] 已删除临时图片文件: {img_path}"
                                            )
                                    except Exception as e:
                                        print(
                                            f"   [图片] 删除图片文件时出错: {e}"
                                        )

                                log_time("商品已跳过AI分析，准备发送通知...")
                                await send_ntfy_notification(
                                    item_data, "商品已跳过AI分析，直接通知"
                                )
                            else:
                                log_time(
                                    f"开始对商品 #{item_data['商品ID']} 进行实时AI分析..."
                                )
                                image_urls = item_data.get("商品图片列表", [])
                                downloaded_image_paths = await download_all_images(
                                    item_data["商品ID"],
                                    image_urls,
                                    task_config.get("task_name", "default"),
                                )

                                ai_analysis_result = None
                                if ai_prompt_text:
                                    try:
                                        ai_analysis_result = await get_ai_analysis(
                                            final_record,
                                            downloaded_image_paths,
                                            prompt_text=ai_prompt_text,
                                        )
                                        if ai_analysis_result:
                                            final_record[
                                                "ai_analysis"
                                            ] = ai_analysis_result
                                            log_time(
                                                f"AI分析完成。推荐状态: {ai_analysis_result.get('is_recommended')}"
                                            )
                                        else:
                                            final_record["ai_analysis"] = {
                                                "error": "AI analysis returned None after retries."
                                            }
                                    except Exception as e:
                                        print(
                                            f"   -> AI分析过程中发生严重错误: {e}"
                                        )
                                        final_record["ai_analysis"] = {"error": str(e)}
                                else:
                                    print("   -> 任务未配置AI prompt，跳过分析。")

                                for img_path in downloaded_image_paths:
                                    try:
                                        if os.path.exists(img_path):
                                            os.remove(img_path)
                                            print(
                                                f"   [图片] 已删除临时图片文件: {img_path}"
                                            )
                                    except Exception as e:
                                        print(
                                            f"   [图片] 删除图片文件时出错: {e}"
                                        )

                                if (
                                    ai_analysis_result
                                    and ai_analysis_result.get("is_recommended")
                                ):
                                    log_time("商品被AI推荐，准备发送通知...")
                                    await send_ntfy_notification(
                                        item_data,
                                        ai_analysis_result.get("reason", "无"),
                                    )

                            await save_to_jsonl(final_record, keyword)

                            processed_links.add(unique_key)
                            processed_item_count += 1
                            log_time(
                                f"商品处理流程完毕。累计处理 {processed_item_count} 个新商品。"
                            )

                            log_time(
                                "[反爬] 执行一次主要的随机延迟以模拟用户浏览间隔..."
                            )
                            await random_sleep(15, 30)
                        else:
                            print(
                                f"   错误: 获取商品详情API响应失败，状态码: {detail_response.status}"
                            )
                            if AI_DEBUG_MODE:
                                print(
                                    f"--- [DETAIL DEBUG] FAILED RESPONSE from {item_data['商品链接']} ---"
                                )
                                try:
                                    print(await detail_response.text())
                                except Exception as e:
                                    print(f"无法读取响应内容: {e}")
                                print("----------------------------------------------------")
                    except PlaywrightTimeoutError:
                        print("   错误: 访问商品详情页或等待API响应超时。")
                    except Exception as e:
                        print(f"   错误: 处理商品详情时发生未知错误: {e}")
                    finally:
                        await detail_page.close()
                        await random_sleep(2, 4)

                if not stop_scraping and page_num < max_pages:
                    print(
                        f"--- 第 {page_num} 页处理完毕，准备翻页。执行一次页面间的长时休息... ---"
                    )
                    await random_sleep(25, 50)

        except PlaywrightTimeoutError as e:
            print(f"\n操作超时错误: 页面元素或网络响应未在规定时间内出现。\n{e}")
        except Exception as e:
            print(f"\n爬取过程中发生未知错误: {e}")
        finally:
            log_time("任务执行完毕，浏览器将在5秒后自动关闭...")
            await asyncio.sleep(5)
            if debug_limit:
                input("按回车键关闭浏览器...")
            await browser.close()

    cleanup_task_images(task_config.get("task_name", "default"))

    return processed_item_count
