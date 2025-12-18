from playwright.sync_api import sync_playwright
from src.stealth_helper import StealthManager

with sync_playwright() as p:
    # 启动浏览器
    browser = p.chromium.launch(
        **StealthManager.get_launch_config()  # ← 使用 stealth 配置
    )
    
    # 创建页面
    page = browser.new_page(
        **StealthManager.get_context_config()  # ← 使用 stealth 上下文
    )
    
    # 应用 Stealth 隐身模式（这是关键！）
    StealthManager.apply_stealth_sync(page)  # ← 一行代码搞定
    
    # 现在可以爬虫了
    page.goto("https://secondhand.xianyu.com/...")
    # ... 你的爬虫逻辑
    
    browser.close()
