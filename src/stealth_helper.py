# src/stealth_helper.py

"""
统一管理 Playwright 的 Stealth 配置，避免被网站轻易识别为自动化脚本。
支持：
- get_launch_config：浏览器启动参数
- get_context_config：上下文参数
- apply_stealth_async：异步模式下对 Page 应用 Stealth
- apply_stealth_sync：同步模式下对 Page 应用 Stealth
"""

from typing import Dict, Any

from playwright.async_api import Page as AsyncPage
from playwright.sync_api import Page as SyncPage


class StealthManager:
    @staticmethod
    def get_launch_config(headless: bool = True) -> Dict[str, Any]:
        """
        浏览器 launch 时的隐身配置。
        在 async_playwright().chromium.launch(...) 或 sync_playwright() 里使用。
        """
        return {
            "headless": headless,
            # 尽量模拟真实用户
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }

    @staticmethod
    def get_context_config() -> Dict[str, Any]:
        """
        browser.new_context(...) 时的隐身配置。
        """
        return {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1280, "height": 720},
        }

    # ----------------- 异步版：在 scraper.py 里用 -----------------

    @staticmethod
    async def apply_stealth_async(page: AsyncPage) -> None:
        """
        在异步 Playwright 环境中，对 Page 应用 Stealth。
        在 scraper.py / login.py 里这样用：
            await StealthManager.apply_stealth_async(page)
        """
        # 删除 webdriver 痕迹
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            """
        )
        # 可以视情况继续扩展更多指纹伪装，这里先保持简单稳定

    # ----------------- 同步版：在测试脚本中用 -----------------

    @staticmethod
    def apply_stealth_sync(page: SyncPage) -> None:
        """
        在同步 Playwright 环境中，对 Page 应用 Stealth。
        例如你刚才的测试脚本：
            with sync_playwright() as p:
                browser = p.chromium.launch(**StealthManager.get_launch_config())
                page = browser.new_page(**StealthManager.get_context_config())
                StealthManager.apply_stealth_sync(page)
        """
        page.add_init_script(
            """
            Object.defineProperty(navigator, '
