"""
闲鱼爬虫优化模块
包含延迟管理、User-Agent轮转、IP封禁检测等反爬虫机制
优化版本 - 修复bug + 增强功能
"""

import asyncio
import random
import time
from functools import wraps
from typing import Tuple, Optional, Callable, Union
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    
    # === 重试延迟（新增）===
    RETRY_BASE_DELAY = (10, 20)          # 重试基础延迟
    ERROR_RECOVERY_DELAY = (30, 60)      # 错误恢复延迟
    
    @staticmethod
    def get_random_delay(delay_tuple: Tuple[float, float]) -> float:
        """
        返回随机延迟（秒）
        
        修复：原代码错误地传递了两个相同参数
        
        Args:
            delay_tuple: (最小延迟, 最大延迟) 元组
            
        Returns:
            float: 随机延迟秒数
        """
        if not isinstance(delay_tuple, (tuple, list)) or len(delay_tuple) != 2:
            raise ValueError(f"delay_tuple 必须是包含两个元素的元组或列表，收到: {delay_tuple}")
        
        min_delay, max_delay = delay_tuple
        
        if min_delay > max_delay:
            min_delay, max_delay = max_delay, min_delay  # 自动交换
        
        return random.uniform(min_delay, max_delay)
    
    @staticmethod
    def get_exponential_backoff_delay(attempt: int, base_delay: float = 5.0, max_delay: float = 300.0) -> float:
        """
        指数退避延迟计算
        
        Args:
            attempt: 当前尝试次数（从1开始）
            base_delay: 基础延迟（秒）
            max_delay: 最大延迟（秒）
            
        Returns:
            float: 计算后的延迟秒数
        """
        delay = base_delay * (2 ** (attempt - 1))
        delay = min(delay, max_delay)
        # 添加随机抖动（±20%）
        jitter = delay * random.uniform(-0.2, 0.2)
        return delay + jitter
    
    @classmethod
    async def smart_delay(cls, delay_type: str = "default", verbose: bool = False):
        """
        智能延迟 - 根据延迟类型自动选择合适的延迟时间
        
        Args:
            delay_type: 延迟类型（navigation/click/filter/pagination等）
            verbose: 是否打印延迟信息
        """
        delay_map = {
            "navigation": cls.NAVIGATION_DELAY,
            "page_load": cls.PAGE_LOAD_WAIT,
            "click": cls.CLICK_DELAY,
            "filter": cls.FILTER_DELAY,
            "pagination": cls.PAGINATION_DELAY,
            "api_wait": cls.API_WAIT_DELAY,
            "detail_api": cls.DETAIL_API_DELAY,
            "item_process": cls.ITEM_PROCESS_DELAY,
            "page_close": cls.PAGE_CLOSE_DELAY,
            "page_between": cls.PAGE_BETWEEN_DELAY,
            "retry": cls.RETRY_BASE_DELAY,
            "error_recovery": cls.ERROR_RECOVERY_DELAY,
            "default": (2, 5)
        }
        
        delay_tuple = delay_map.get(delay_type, delay_map["default"])
        delay_seconds = cls.get_random_delay(delay_tuple)
        
        if verbose:
            print(f"⏱️  [{delay_type}] 延迟 {delay_seconds:.2f} 秒...")
        
        await asyncio.sleep(delay_seconds)


# ============================================================================
# 2️⃣ User-Agent 管理器（核心）
# ============================================================================

class UserAgentManager:
    """多样化User-Agent轮转 - 规避指纹识别"""
    
    # 扩展 UA 池 - 增加更多真实浏览器UA
    USER_AGENTS = [
        # Chrome - Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        
        # Chrome - macOS
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_6_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        
        # Chrome - Linux
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Ubuntu; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        
        # Firefox - Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        
        # Firefox - macOS
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13.0; rv:120.0) Gecko/20100101 Firefox/120.0',
        
        # Safari - macOS
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
        
        # Edge - Windows
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        
        # Mobile - iOS
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
        
        # Mobile - Android
        'Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    ]
    
    def __init__(self):
        self.current_index = 0
        self.usage_count = {}  # 记录每个UA的使用次数
        self.last_used_time = {}  # 记录每个UA的最后使用时间
    
    @staticmethod
    def get_random_ua() -> str:
        """随机返回一个User-Agent"""
        return random.choice(UserAgentManager.USER_AGENTS)
    
    def get_next_ua(self) -> str:
        """轮转返回User-Agent"""
        ua = self.USER_AGENTS[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.USER_AGENTS)
        
        # 记录使用信息
        self.usage_count[ua] = self.usage_count.get(ua, 0) + 1
        self.last_used_time[ua] = datetime.now()
        
        return ua
    
    def get_weighted_random_ua(self) -> str:
        """
        加权随机UA - 优先选择使用较少的UA
        
        Returns:
            str: User-Agent 字符串
        """
        # 计算权重：使用次数越少权重越高
        weights = []
        for ua in self.USER_AGENTS:
            usage = self.usage_count.get(ua, 0)
            weight = 1.0 / (usage + 1)  # 避免除零
            weights.append(weight)
        
        # 归一化权重
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]
        
        # 加权随机选择
        selected_ua = random.choices(self.USER_AGENTS, weights=weights, k=1)[0]
        
        # 记录使用信息
        self.usage_count[selected_ua] = self.usage_count.get(selected_ua, 0) + 1
        self.last_used_time[selected_ua] = datetime.now()
        
        return selected_ua
    
    def get_desktop_ua(self) -> str:
        """仅返回桌面浏览器UA"""
        desktop_uas = [ua for ua in self.USER_AGENTS if 'Mobile' not in ua and 'iPhone' not in ua and 'Android' not in ua]
        return random.choice(desktop_uas)
    
    def get_mobile_ua(self) -> str:
        """仅返回移动浏览器UA"""
        mobile_uas = [ua for ua in self.USER_AGENTS if 'Mobile' in ua or 'iPhone' in ua or 'Android' in ua]
        return random.choice(mobile_uas)
    
    def get_usage_stats(self) -> dict:
        """
        获取UA使用统计信息
        
        Returns:
            dict: 包含使用统计的字典
        """
        return {
            "total_uas": len(self.USER_AGENTS),
            "usage_count": self.usage_count.copy(),
            "most_used": max(self.usage_count.items(), key=lambda x: x[1]) if self.usage_count else None,
            "least_used": min(self.usage_count.items(), key=lambda x: x[1]) if self.usage_count else None,
        }


# ============================================================================
# 3️⃣ IP黑名单检测器（核心）
# ============================================================================

class IPBlockerDetector:
    """检测和应对IP被黑名单/验证码的情况"""
    
    BLOCKER_KEYWORDS = {
        "验证码": ["验证", "验证码", "baxia", "middleware", "slide", "滑块", "拖动", "captcha"],
        "IP被封": ["访问异常", "访问频繁", "请稍候", "被限制", "429", "too many requests", "rate limit"],
        "账户异常": ["异常", "安全", "已禁用", "用户异常", "账号", "风险"],
        "登录要求": ["请登录", "需要登录", "sign in", "login required"],
    }
    
    def __init__(self, max_consecutive_fails: int = 3):
        self.consecutive_fails = 0
        self.max_consecutive_fails = max_consecutive_fails
        self.total_blocks = 0
        self.block_history = []  # 记录被封禁的时间
        self.last_block_time = None
    
    async def check
