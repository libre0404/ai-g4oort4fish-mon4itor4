# src/optimization.py
import asyncio
import random
from functools import wraps
from typing import Tuple

# ============================================================================
# 1️⃣ 延迟配置管理器（核心）
# ============================================================================

class DelayConfig:
    """集中管理所有延迟参数 - 反爬虫的关键"""
    
    # === 基础延迟 ===
    NAVIGATION_DELAY = (6, 12)           # 页面导航
    PAGE_LOAD_WAIT = (3, 6)              # 页面加载
    
    # === 交互延迟 ===
    CLICK_DELAY = (1, 3)                 # 点击前延迟
    FILTER_DELAY = (5, 10)               # 筛选操作
    PAGINATION_DELAY = (25, 50)          # 翻页操作（最关键！）
    
    # === API等待 ===
    API_WAIT_DELAY = (4, 9)              # 等待API返回
    DETAIL_API_DELAY = (5, 11)           # 商品详情API
    
    # === 页面处理 ===
    ITEM_PROCESS_DELAY = (15, 35)        # 处理单个商品
    PAGE_CLOSE_DELAY = (3, 6)            # 关闭页面后延迟
    PAGE_BETWEEN_DELAY = (25, 50)        # 页面间的休息
    
    @staticmethod
    def get_random_delay(delay_tuple):
        """返回随机延迟（秒）"""
        return random.uniform(delay_tuple, delay_tuple)


# ============================================================================
# 2️⃣ User-Agent 管理器（核心）
# ============================================================================

class UserAgentManager:
    """多样化User-Agent轮转 - 规避指纹识别"""
    
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_6_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
    ]
    
    def __init__(self):
        self.current_index = 0
    
    @staticmethod
    def get_random_ua():
        """随机返回一个User-Agent"""
        return random.choice(UserAgentManager.USER_AGENTS)
    
    def get_next_ua(self):
        """轮转返回User-Agent"""
        ua = self.USER_AGENTS[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.USER_AGENTS)
        return ua


# ============================================================================
# 3️⃣ IP黑名单检测器（核心）
# ============================================================================

class IPBlockerDetector:
    """检测和应对IP被黑名单/验证码的情况"""
    
    BLOCKER_KEYWORDS = {
        "验证码": ["验证", "验证码", "baxia", "middleware", "slide"],
        "IP被封": ["访问异常", "访问频繁", "请稍候", "被限制", "429"],
        "账户异常": ["异常", "安全", "已禁用", "用户异常"],
    }
    
    def __init__(self, max_consecutive_fails=3):
        self.consecutive_fails = 0
        self.max_consecutive_fails = max_consecutive_fails
    
    async def check_page_blocked(self, page) -> Tuple[bool, str]:
        """检查页面是否被阻止，返回 (是否被阻止, 阻止类型)"""
        try:
            page_content = await page.content()
            for block_type, keywords in self.BLOCKER_KEYWORDS.items():
                for keyword in keywords:
                    if keyword.lower() in page_content.lower():
                        return True, block_type
            return False, "normal"
        except Exception as e:
            print(f"⚠️  检查页面状态时出错: {e}")
            return False, "unknown"
    
    async def handle_blocked(self):
        """处理被黑名单的情况"""
        self.consecutive_fails += 1
        if self.consecutive_fails >= self.max_consecutive_fails:
            sleep_seconds = min(600 * self.consecutive_fails, 3600)
            sleep_minutes = sleep_seconds / 60
            print(f"🛑 连续 {self.consecutive_fails} 次被检测，将休眠 {sleep_minutes:.1f} 分钟...")
            await asyncio.sleep(sleep_seconds)
            self.consecutive_fails = 0
    
    def reset_fails(self):
        """成功则重置计数"""
        self.consecutive_fails = 0
        print("✅ 恢复正常，重置失败计数")
