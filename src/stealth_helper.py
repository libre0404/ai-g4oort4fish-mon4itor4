"""
Playwright Stealth 隐身模式助手
用于绕过网站的反爬虫检测

适配场景：
- 闲鱼/淘宝等阿里系电商平台
- 武汉地区用户（地理位置设为武汉）
- 支持同步和异步两种模式
"""

from typing import Dict, Any


class StealthManager:
    """
    Playwright 隐身模式管理器
    
    提供三个核心方法：
    1. get_launch_config() - 浏览器启动配置
    2. get_context_config() - 浏览器上下文配置
    3. apply_stealth_sync() / apply_stealth_async() - 注入反检测脚本
    """
    
    @staticmethod
    def get_launch_config(headless: bool = True) -> Dict[str, Any]:
        """
        获取浏览器启动配置
        
        Args:
            headless: 是否无头模式（默认True）
                     - True: 后台运行，不显示浏览器窗口
                     - False: 显示浏览器窗口（推荐调试时使用）
            
        Returns:
            Dict: 浏览器启动配置字典
            
        Example:
            browser = p.chromium.launch(**StealthManager.get_launch_config())
        """
        return {
            "headless": headless,
            "args": [
                # 禁用自动化控制特征（最关键）
                '--disable-blink-features=AutomationControlled',
                
                # 性能优化相关
                '--disable-dev-shm-usage',  # 禁用/dev/shm使用
                '--disable-setuid-sandbox',  # 禁用setuid沙盒
                '--no-sandbox',  # 禁用沙盒模式（Docker必需）
                
                # 绕过检测相关
                '--disable-web-security',  # 禁用web安全策略
                '--disable-features=IsolateOrigins,site-per-process',  # 禁用站点隔离
                '--disable-infobars',  # 禁用"Chrome正在被自动化软件控制"提示
                
                # 窗口设置
                '--window-size=1920,1080',  # 设置窗口大小
                '--start-maximized',  # 启动时最大化
                
                # 其他优化
                '--disable-gpu',  # 禁用GPU加速（无头模式推荐）
                '--disable-software-rasterizer',  # 禁用软件光栅化
                '--disable-extensions',  # 禁用扩展
                '--disable-plugins',  # 禁用插件
            ]
        }
    
    @staticmethod
    def get_context_config() -> Dict[str, Any]:
        """
        获取浏览器上下文配置
        
        包含：
        - 视口大小
        - 语言和时区（中文+武汉时区）
        - 地理位置（武汉坐标）
        - 权限设置
        
        Returns:
            Dict: 浏览器上下文配置字典
            
        Example:
            context = browser.new_context(**StealthManager.get_context_config())
            # 或在 new_page 时使用
            page = context.new_page(**StealthManager.get_context_config())
        """
        return {
            # 视口设置
            "viewport": {
                "width": 1920,
                "height": 1080
            },
            
            # 语言和时区
            "locale": "zh-CN",  # 简体中文
            "timezone_id": "Asia/Shanghai",  # 中国标准时间
            
            # 地理位置（武汉市中心坐标）
            "permissions": ["geolocation"],
            "geolocation": {
                "latitude": 30.5928,   # 武汉纬度
                "longitude": 114.3055  # 武汉经度
            },
            
            # 显示设置
            "color_scheme": "light",  # 浅色主题
            
            # 功能开关
            "accept_downloads": True,  # 允许下载
            "ignore_https_errors": True,  # 忽略HTTPS错误
            "java_script_enabled": True,  # 启用JavaScript
            
            # 额外请求头（可选）
            "extra_http_headers": {
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            }
        }
    
    @staticmethod
    def apply_stealth_sync(page):
        """
        应用 Stealth 隐身模式（同步版本）
        
        注入反检测JavaScript脚本，覆盖浏览器指纹
        
        Args:
            page: Playwright 同步页面对象（from playwright.sync_api）
            
        Example:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(**StealthManager.get_launch_config())
                page = browser.new_page(**StealthManager.get_context_config())
                StealthManager.apply_stealth_sync(page)  # ← 应用隐身
                page.goto("https://www.goofish.com")
        """
        # 注入反检测脚本（在页面加载前执行）
        page.add_init_script("""
            // ===== 1. 覆盖 navigator.webdriver =====
            // 这是最关键的检测点
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined  // 正常浏览器返回 undefined
            });
            
            // ===== 2. 覆盖 navigator.plugins =====
            // 模拟真实浏览器的插件列表
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]  // 假装有5个插件
            });
            
            // ===== 3. 覆盖 navigator.languages =====
            // 模拟中文用户
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            
            // ===== 4. 添加 window.chrome 对象 =====
            // Chrome 浏览器特有的对象
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // ===== 5. 覆盖 permissions API =====
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // ===== 6. 移除 Playwright 特征 =====
            delete navigator.__proto__.webdriver;
            
            // ===== 7. 模拟屏幕信息 =====
            Object.defineProperty(window, 'devicePixelRatio', {
                get: () => 2  // Retina 屏幕
            });
            
            // ===== 8. 覆盖 Canvas 指纹 =====
            const getContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                if (type === '2d') {
                    const context = getContext.call(this, type, ...args);
                    const getImageData = context.getImageData;
                    context.getImageData = function(...args) {
                        const imageData = getImageData.apply(this, args);
                        // 添加微小的随机噪声
                        for (let i = 0; i < imageData.data.length; i += 4) {
                            imageData.data[i] += Math.floor(Math.random() * 10) - 5;
                        }
                        return imageData;
                    };
                    return context;
                }
                return getContext.call(this, type, ...args);
            };
            
            // ===== 9. 覆盖 WebGL 指纹 =====
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                // 37445: UNMASKED_VENDOR_WEBGL
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                // 37446: UNMASKED_RENDERER_WEBGL
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter.call(this, parameter);
            };
            
            // ===== 10. 隐藏自动化框架痕迹 =====
            // 删除可能暴露的属性
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)
    
    @staticmethod
    async def apply_stealth_async(page):
        """
        应用 Stealth 隐身模式（异步版本）
        
        注入反检测JavaScript脚本，覆盖浏览器指纹
        
        Args:
            page: Playwright 异步页面对象（from playwright.async_api）
            
        Example:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(**StealthManager.get_launch_config())
                page = await browser.new_page(**StealthManager.get_context_config())
                await StealthManager.apply_stealth_async(page)  # ← 异步应用
                await page.goto("https://www.goofish.com")
        """
        # 注入反检测脚本（异步版本，脚本内容与同步版本相同）
        await page.add_init_script("""
            // ===== 1. 覆盖 navigator.webdriver =====
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // ===== 2. 覆盖 navigator.plugins =====
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // ===== 3. 覆盖 navigator.languages =====
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            
            // ===== 4. 添加 window.chrome 对象 =====
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // ===== 5. 覆盖 permissions API =====
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // ===== 6. 移除 Playwright 特征 =====
            delete navigator.__proto__.webdriver;
            
            // ===== 7. 模拟屏幕信息 =====
            Object.defineProperty(window, 'devicePixelRatio', {
                get: () => 2
            });
            
            // ===== 8. 覆盖 Canvas 指纹 =====
            const getContext = HTMLCanvasElement.prototype.getContext;
            HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                if (type === '2d') {
                    const context = getContext.call(this, type, ...args);
                    const getImageData = context.getImageData;
                    context.getImageData = function(...args) {
                        const imageData = getImageData.apply(this, args);
                        for (let i = 0; i < imageData.data.length; i += 4) {
                            imageData.data[i] += Math.floor(Math.random() * 10) - 5;
                        }
                        return imageData;
                    };
                    return context;
                }
                return getContext.call(this, type, ...args);
            };
            
            // ===== 9. 覆盖 WebGL 指纹 =====
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) {
                    return 'Intel Inc.';
                }
                if (parameter === 37446) {
                    return 'Intel Iris OpenGL Engine';
                }
                return getParameter.call(this, parameter);
            };
            
            // ===== 10. 隐藏自动化框架痕迹 =====
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)


# ============================================================================
# 便捷函数（可选）
# ============================================================================

def apply_stealth_to_page_sync(page):
    """
    便捷函数：为同步页面应用隐身模式
    
    Args:
        page: Playwright 同步页面对象
    """
    StealthManager.apply_stealth_sync(page)


async def apply_stealth_to_page_async(page):
    """
    便捷函数：为异步页面应用隐身模式
    
    Args:
        page: Playwright 异步页面对象
    """
    await StealthManager.apply_stealth_async(page)


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    # ===== 同步版本示例 =====
    print("=" * 60)
    print("Stealth Helper 使
