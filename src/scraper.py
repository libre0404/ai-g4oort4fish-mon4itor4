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
    """
    采集闲鱼用户的完整信息
    
    Args:
        context: Playwright 浏览器上下文
        user_id: 用户ID
        
    Returns:
        dict: 包含用户信息的字典
    """
    print(f"   -> 开始采集用户ID: {user_id} 的完整信息...")
    profile_data = {}

    # 使用 Stealth 配置创建页面
    page = await context.new_page(**StealthManager.get_context_config())
    await StealthManager.apply_stealth_async(page)

    # 为各项异步任务准备Future和数据容器
    head_api_future = asyncio.get_event_loop().create_future()

    all_items, all_ratings = [], []
    stop_item_scrolling, stop_rating_scrolling = asyncio.Event(), asyncio.Event()

    async def handle_response(response: Response):
        """处理API响应的回调函数"""
        # 捕获头部摘要API
        if "mtop.idle.web.user.page.head" in response.url and not head_api_future.done():
            try:
                head_api_future.set_result(await response.json())
                print(f"      [API捕获] 用户头部信息... 成功")
            except Exception as e:
                if not head_api_future.done():
                    head_api_future.set_exception(e)

        # 捕获商品列表API
        elif "mtop.idle.web.xyh.item.list" in response.url:
            try:
                data = await response.json()
                all_items.extend(data.get('data', {}).get('cardList', []))
                print(f"      [API捕获] 商品列表... 当前已捕获 {len(all_items)} 件")
                if not data.get('data', {}).get('nextPage', True):
                    stop_item_scrolling.set()
            except Exception as e:
                stop_item_scrolling.set()

        # 捕获评价列表API
        elif "mtop.idle.web.trade.rate.list" in response.url:
            try:
                data = await response.json()
                all_ratings.extend(data.get('data', {}).get('cardList', []))
                print(f"      [API捕获] 评价列表... 当前已捕获 {len(all_ratings)} 条")
                if not data.get('data', {}).get('nextPage', True):
                    stop_rating_scrolling.set()
            except Exception as e:
                stop_rating_scrolling.set()

    page.on("response", handle_response)

    try:
        # --- 任务1: 导航并采集头部信息 ---
        await page.goto(
            f"https://www.goofish.com/personal?userId={user_id}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        head_data = await asyncio.wait_for(head_api_future, timeout=15)
        profile_data = await parse_user_head_data(head_data)

        # --- 任务2: 滚动加载所有商品 (默认页面) ---
        print("      [采集阶段] 开始采集该用户的商品列表...")
        await DelayConfig.smart_delay("api_wait")  # 等待第一页商品API完成
        
        while not stop_item_scrolling.is_set():
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            try:
                await asyncio.wait_for(stop_item_scrolling.wait(), timeout=8)
            except asyncio.TimeoutError:
                print("      [滚动超时] 商品列表可能已加载完毕。")
                break
        
        profile_data["卖家发布的商品列表"] = await _parse_user_items_data(all_items)

        # --- 任务3: 点击并采集所有评价 ---
        print("      [采集阶段] 开始采集该用户的评价列表...")
        rating_tab_locator = page.locator("//div[text()='信用及评价']/ancestor::li")
        if await rating_tab_locator.count() > 0:
            await rating_tab_locator.click()
            await DelayConfig.smart_delay("api_wait")  # 等待第一页评价API完成

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
    """
    【核心执行器】
    根据单个任务配置，异步爬取闲鱼商品数据，并对每个新发现的商品进行实时的、独立的AI分析和通知。
    
    Args:
        task_config: 任务配置字典
        debug_limit: 调试模式下的商品数量限制（0表示无限制）
        
    Returns:
        int: 处理的商品数量
    """
    keyword = task_config['keyword']
    max_pages = task_config.get('max_pages', 1)
    personal_only = task_config.get('personal_only', False)
    min_price = task_config.get('min_price')
    max_price = task_config.get('max_price')
    ai_prompt_text = task_config.get('ai_prompt_text', '')

    processed_item_count = 0
    stop_scraping = False

    # 【优化】初始化反爬虫检测器
    ip_detector = IPBlockerDetector(max_consecutive_fails=3)
    
    # 加载历史记录以去重
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
        # 根据配置启动不同的浏览器
        if LOGIN_IS_EDGE:
            browser = await p.chromium.launch(
                headless=RUN_HEADLESS,
                channel="msedge",
                **StealthManager.get_launch_config(headless=RUN_HEADLESS),
            )
        else:
            if RUNNING_IN_DOCKER:
                browser = await p.chromium.launch(
                    headless=RUN_HEADLESS,
                    **StealthManager.get_launch_config(headless=RUN_HEADLESS),
                )
            else:
                browser = await p.chromium.launch(
                    headless=RUN_HEADLESS,
                    channel="chrome",
                    **StealthManager.get_launch_config(headless=RUN_HEADLESS),
                )

        # 创建浏览器上下文，集成 Stealth 和随机 UA
        random_ua = get_random_user_agent()
        print(f"🔄 使用User-Agent: {random_ua[:80]}...")

        context = await browser.new_context(
            storage_state=STATE_FILE,
            user_agent=random_ua,
            **StealthManager.get_context_config(),
        )

        # 创建主页面
        page = await context.new_page(**StealthManager.get_context_config())
        await StealthManager.apply_stealth_async(page)

        try:
            log_time("步骤 1 - 直接导航到搜索结果页...")
            
            # 构建搜索URL
            params = {'q': keyword}
            search_url = f"https://www.goofish.com/search?{urlencode(params)}"
            log_time(f"目标URL: {search_url}")

            # 导航并捕获初始搜索API数据
            async with page.expect_response(
                lambda r: API_URL_PATTERN in r.url, 
                timeout=30000
            ) as response_info:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)

            initial_response = await response_info.value

            # 等待页面加载出关键筛选元素
            await page.wait_for_selector('text=新发布', timeout=15000)

            # --- 检查是否存在验证弹窗 ---
            baxia_dialog = page.locator("div.baxia-dialog-mask")
            middleware_widget = page.locator("div.J_MIDDLEWARE_FRAME_WIDGET")
            
            is_blocked = False
            
            try:
                await baxia_dialog.wait_for(state='visible', timeout=2000)
                is_blocked = True
                print("\n" + "="*60)
                print("【反爬虫检测】检测到 baxia-dialog 验证弹窗")
                print("="*60)
                await ip_detector.handle_blocked()
                print("建议：")
                print("1. 停止脚本一段时间再试")
                print("2. 设置 RUN_HEADLESS=false 使用非无头模式")
                print("3. 检查代理设置或更换IP")
                print(f"任务 '{keyword}' 将在此处中止")
                print("="*60 + "\n")
            except PlaywrightTimeoutError:
                pass

            if not is_blocked:
                try:
                    await middleware_widget.wait_for(state='visible', timeout=2000)
                    is_blocked = True
                    print("\n" + "="*60)
                    print("【反爬虫检测】检测到 J_MIDDLEWARE_FRAME_WIDGET 验证弹窗")
                    print("="*60)
                    await ip_detector.handle_blocked()
                    print("建议：")
                    print("1. 停止脚本一段时间再试")
                    print("2. 更新登录状态文件")
                    print("3. 降低任务执行频率")
                    print(f"任务 '{keyword}' 将在此处中止")
                    print("="*60 + "\n")
                except PlaywrightTimeoutError:
                    # 未检测到封禁，重置失败计数
                    ip_detector.reset_fails()
                    pass

            if is_blocked:
                await browser.close()
                return processed_item_count

            # --- 关闭广告弹窗 ---
            try:
                await page.click("div[class*='closeIconBg']", timeout=3000)
                print("LOG: 已关闭广告弹窗。")
            except PlaywrightTimeoutError:
                print("LOG: 未检测到广告弹窗。")

            # --- 步骤 2: 应用筛选条件 ---
            final_response = None
            log_time("步骤 2 - 应用筛选条件...")
            
            # 点击"新发布"筛选
