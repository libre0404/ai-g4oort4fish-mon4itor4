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
from src.stealth_helper import StealthManager  # ← 绝对导入

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

    # 这里改：new_page 用 get_page_config()
    page = await context.new_page(**StealthManager.get_page_config())
    await StealthManager.apply_stealth_async(page)

    head_api_future = asyncio.get_event_loop().create_future()

    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        if "mtop.idle.web.user.page.head" in response.url and not head_api_future.done():
            try:
                head_api_future.set_result(await response.json())
                print(f"      [API捕获] 用户头部信息... 成功")
            except Exception as e:
                if not head_api_future.done():
                    head_api_future.set_exception(e)

        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get('data', {}).get('cardList', []))
                print(f"      [API捕获] 商品列表... 当前已捕获 {len(all_items)} 件")
                if not data.get('data', {}).get('nextPage', True):
                    stop_item_scrolling.set()
            except Exception:
                stop_item_scrolling.set()

        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get('data', {}).get('cardList', []))
                print(f"      [API捕获] 评价列表... 当前已捕获 {len(all_ratings)} 条")
                if not data.get('data', {}).get('nextPage', True):
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
        await random_sleep(2, 4)
        while not stop_item_scrolling.is_set():
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
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
            await random_sleep(3, 5)

            while not stop_rating_scrolling.is_set():
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                try:
                    await asyncio.wait_for(stop_rating_scrolling.wait(), timeout=8)
                except asyncio.TimeoutError:
                    print("      [滚动超时] 评价列表可能已加载完毕。")
                    break

            profile_data['卖家收到的评价列表'] = await parse_ratings_data(all_ratings)
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
    keyword = task_config['keyword']
    max_pages = task_config.get('max_pages', 1)
    personal_only = task_config.get('personal_only', False)
    min_price = task_config.get('min_price')
    max_price = task_config.get('max_price')
    ai_prompt_text = task_config.get('ai_prompt_text', '')

    processed_item_count = 0
    stop_scraping = False

    processed_links = set()
    output_filename = os.path.join("jsonl", f"{keyword.replace(' ', '_')}_full_data.jsonl")
    if os.path.exists(output_filename):
        print(f"LOG: 发现已存在文件 {output_filename}，正在加载历史记录以去重...")
        try:
            with open(output_filename, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        link = record.get('商品信息', {}).get('商品链接', '')
                        if link:
                            processed_links.add(get_link_unique_key(link))
                    except json.JSONDecodeError:
                        print(f"   [警告] 文件中有一行无法解析为JSON，已跳过。")
            print(f"LOG: 加载完成，已记录 {len(processed_links)} 个已处理过的商品。")
        except IOError as e:
            print(f"   [警告] 读取历史文件时发生错误: {e}")
    else:
        print(f"LOG: 输出文件 {output_filename} 不存在，将创建新文件。")

    async with async_playwright() as p:
        if LOGIN_IS_EDGE:
            browser = await p.chromium.launch(
                channel="msedge",
                **StealthManager.get_launch_config(headless=RUN_HEADLESS),
            )
        else:
            if RUNNING_IN_DOCKER:
                browser = await p.chromium.launch(
                    **StealthManager.get_launch_config(headless=RUN_HEADLESS),
                )
            else:
                browser = await p.chromium.launch(
                    channel="chrome",
                    **StealthManager.get_launch_config(headless=RUN_HEADLESS),
                )

        random_ua = get_random_user_agent()
        print(f"🔄 使用User-Agent: {random_ua[:80]}...")

        # 这里 context 继续用 get_context_config（有 locale/时区）
        context = await browser.new_context(
            storage_state=STATE_FILE,
            user_agent=random_ua,
            **StealthManager.get_context_config(),
        )

        # 这里改：new_page 用 get_page_config()
        page = await context.new_page(**StealthManager.get_page_config())
        await StealthManager.apply_stealth_async(page)

        try:
            log_time("步骤 1 - 直接导航到搜索结果页...")
            params = {'q': keyword}
            search_url = f"https://www.goofish.com/search?{urlencode(params)}"
            log_time(f"目标URL: {search_url}")

            async with page.expect_response(lambda r: API_URL_PATTERN in r.url, timeout=30000) as response_info:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

            initial_response = await response_info.value
            await page.wait_for_selector('text=新发布', timeout=15000)

            # ……后面所有逻辑保持你原来的代码不变……
            # 包括弹窗检测、过滤条件、翻页、详情页、AI 分析等
            # 只要在新建详情页时也记得用 get_page_config 即可：

            # 示例（在处理商品详情处）：
            # detail_page = await context.new_page(**StealthManager.get_page_config())
            # await StealthManager.apply_stealth_async(detail_page)
            # ...

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

    cleanup_task_images(task_config.get('task_name', 'default'))

    return processed_item_count
