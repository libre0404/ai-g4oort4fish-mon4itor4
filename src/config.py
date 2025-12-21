"""
闲鱼监控工具 - 配置文件
增强版 - 通用监控 + Apple 产品深度优化
适配内容创作者的二手设备采购需求
"""

import os
import sys
from typing import Optional, Dict, List, Tuple
from datetime import datetime

from dotenv import load_dotenv
from openai import AsyncOpenAI

from src.optimization import DelayConfig, UserAgentManager

# =============================================================================
# User-Agent 管理
# =============================================================================

ua_manager = UserAgentManager()

def get_random_user_agent() -> str:
    """获取随机User-Agent"""
    return ua_manager.get_random_ua()

def get_next_user_agent() -> str:
    """轮转获取User-Agent"""
    return ua_manager.get_next_ua()

# =============================================================================
# 环境变量加载
# =============================================================================

load_dotenv()

# =============================================================================
# 文件路径与目录配置
# =============================================================================

STATE_FILE = "xianyu_state.json"
IMAGE_SAVE_DIR = "images"
CONFIG_FILE = "config.json"
os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)

# 任务隔离的临时图片目录前缀
TASK_IMAGE_DIR_PREFIX = "task_images_"

# =============================================================================
# API URL 配置
# =============================================================================

API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search"
DETAIL_API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail"

# =============================================================================
# 环境变量读取
# =============================================================================

# AI 配置
API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL_NAME = os.getenv("OPENAI_MODEL_NAME")
PROXY_URL = os.getenv("PROXY_URL")

# 通知渠道配置
NTFY_TOPIC_URL = os.getenv("NTFY_TOPIC_URL")
GOTIFY_URL = os.getenv("GOTIFY_URL")
GOTIFY_TOKEN = os.getenv("GOTIFY_TOKEN")
BARK_URL = os.getenv("BARK_URL")
WX_BOT_URL = os.getenv("WX_BOT_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Webhook 配置
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_METHOD = os.getenv("WEBHOOK_METHOD", "POST").upper()
WEBHOOK_HEADERS = os.getenv("WEBHOOK_HEADERS")
WEBHOOK_CONTENT_TYPE = os.getenv("WEBHOOK_CONTENT_TYPE", "JSON").upper()
WEBHOOK_QUERY_PARAMETERS = os.getenv("WEBHOOK_QUERY_PARAMETERS")
WEBHOOK_BODY = os.getenv("WEBHOOK_BODY")

# 运行模式配置
PCURL_TO_MOBILE = os.getenv("PCURL_TO_MOBILE", "false").lower() == "true"
RUN_HEADLESS = os.getenv("RUN_HEADLESS", "true").lower() != "false"
LOGIN_IS_EDGE = os.getenv("LOGIN_IS_EDGE", "false").lower() == "true"
RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true"

# AI 功能开关
AI_DEBUG_MODE = os.getenv("AI_DEBUG_MODE", "false").lower() == "true"
SKIP_AI_ANALYSIS = os.getenv("SKIP_AI_ANALYSIS", "false").lower() == "true"
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "false").lower() == "true"
ENABLE_RESPONSE_FORMAT = os.getenv("ENABLE_RESPONSE_FORMAT", "true").lower() == "true"

# Apple 产品增强分析开关（新增）
ENABLE_APPLE_ENHANCED_ANALYSIS = os.getenv("ENABLE_APPLE_ENHANCED_ANALYSIS", "true").lower() == "true"
ENABLE_VIDEO_EDITING_SCORING = os.getenv("ENABLE_VIDEO_EDITING_SCORING", "true").lower() == "true"
ENABLE_AUTHENTICITY_CHECK = os.getenv("ENABLE_AUTHENTICITY_CHECK", "true").lower() == "true"

# =============================================================================
# HTTP 请求头配置
# =============================================================================

def get_image_download_headers() -> Dict[str, str]:
    """
    动态生成图片下载请求头 - 每次都随机
    
    这个函数会在每次调用时生成新的User-Agent，
    确保每次请求都不同，规避反爬虫检测
    """
    return {
        'User-Agent': get_random_user_agent(),
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Referer': 'https://www.goofish.com/',
    }

# 保留静态headers作为fallback（修复原代码重复问题）
IMAGE_DOWNLOAD_HEADERS_FALLBACK = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0',
    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

# =============================================================================
# 代理配置
# =============================================================================

def setup_proxy() -> Optional[Dict[str, str]]:
    """配置HTTP/HTTPS代理"""
    if PROXY_URL:
        print(f"[代理] 正在为请求配置代理: {PROXY_URL}")
        os.environ['HTTP_PROXY'] = PROXY_URL
        os.environ['HTTPS_PROXY'] = PROXY_URL
        return {'http': PROXY_URL, 'https': PROXY_URL}
    return None

PROXY_CONFIG = setup_proxy()

# =============================================================================
# OpenAI 客户端初始化（优化版）
# =============================================================================

client: Optional[AsyncOpenAI] = None

def initialize_ai_client() -> Tuple[Optional[AsyncOpenAI], List[str]]:
    """初始化 OpenAI 客户端"""
    warnings = []
    
    if not all([BASE_URL, MODEL_NAME]):
        warning_msg = "警告：未在 .env 文件中完整设置 OPENAI_BASE_URL 和 OPENAI_MODEL_NAME。AI相关功能将被禁用。"
        warnings.append(warning_msg)
        print(f"[配置] {warning_msg}")
        return None, warnings
    
    try:
        if PROXY_URL:
            print(f"[AI客户端] 使用代理: {PROXY_URL}")
        
        ai_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
        print(f"[AI客户端] 初始化成功 - 模型: {MODEL_NAME}")
        return ai_client, warnings
        
    except Exception as e:
        error_msg = f"初始化 OpenAI 客户端时出错: {e}"
        warnings.append(error_msg)
        print(f"[错误] {error_msg}")
        return None, warnings

# 初始化客户端
client, _init_warnings = initialize_ai_client()

# 兼容原代码的检查逻辑
if not client:
    pass

# 检查关键配置
if not all([BASE_URL, MODEL_NAME]) and 'prompt_generator.py' in sys.argv[0]:
    sys.exit("错误：请确保在 .env 文件中完整设置了 OPENAI_BASE_URL 和 OPENAI_MODEL_NAME。(OPENAI_API_KEY 对于某些服务是可选的)")

# =============================================================================
# AI 请求参数构建（修复 ENABLE_THINKING 逻辑）
# =============================================================================

def get_ai_request_params(**kwargs) -> Dict:
    """
    构建AI请求参数，根据环境变量决定是否添加特定参数
    
    修复：ENABLE_THINKING 逻辑错误（原代码传递了 False）
    """
    # 修复：当启用 THINKING 时应该传递 True
    if ENABLE_THINKING:
        kwargs["extra_body"] = {"enable_thinking": True}
    
    # 如果禁用 response_format，则移除该参数
    if not ENABLE_RESPONSE_FORMAT and "response_format" in kwargs:
        del kwargs["response_format"]
    
    # 添加模型名称（如果未指定）
    if "model" not in kwargs and MODEL_NAME:
        kwargs["model"] = MODEL_NAME
    
    return kwargs

# =============================================================================
# Apple 产品数据库 - Mac 系列
# =============================================================================

MAC_MODELS_DATABASE = {
    "MacBook Pro": {
        # M4 系列 (2024)
        "M4 Max": {
            "release_year": 2024,
            "typical_price_range": (18000, 32000),
            "video_editing_score": 100,
            "recommended_for": "专业4K/8K视频剪辑、多轨调色",
            "software_support": ["Final Cut Pro", "DaVinci Resolve", "Premiere Pro"],
        },
        "M4 Pro": {
            "release_year": 2024,
            "typical_price_range": (12000, 20000),
            "video_editing_score": 95,
            "recommended_for": "4K视频剪辑、多机位剪辑",
            "software_support": ["Final Cut Pro", "Premiere Pro"],
        },
        "M4": {
            "release_year": 2024,
            "typical_price_range": (10000, 16000),
            "video_editing_score": 90,
            "recommended_for": "1080p/4K基础剪辑",
            "software_support": ["Final Cut Pro", "iMovie", "Premiere Pro"],
        },

        # M3 系列 (2023)
        "M3 Max": {
            "release_year": 2023,
            "typical_price_range": (16000, 28000),
            "video_editing_score": 98,
            "recommended_for": "专业4K视频剪辑、调色",
            "software_support": ["Final Cut Pro", "DaVinci Resolve", "Premiere Pro"],
        },
        "M3 Pro": {
            "release_year": 2023,
            "typical_price_range": (11000, 17000),
            "video_editing_score": 93,
            "recommended_for": "4K视频剪辑",
            "software_support": ["Final Cut Pro", "Premiere Pro"],
        },
        "M3": {
            "release_year": 2023,
            "typical_price_range": (8000, 13000),
            "video_editing_score": 88,
            "recommended_for": "1080p视频剪辑、轻度4K",
            "software_support": ["Final Cut Pro", "iMovie"],
        },

        # M2 系列 (2022-2023)
        "M2 Max": {
            "release_year": 2023,
            "typical_price_range": (13000, 23000),
            "video_editing_score": 95,
            "recommended_for": "专业4K视频剪辑",
            "software_support": ["Final Cut Pro", "DaVinci Resolve", "Premiere Pro"],
        },
        "M2 Pro": {
            "release_year": 2023,
            "typical_price_range": (9000, 15000),
            "video_editing_score": 90,
            "recommended_for": "4K视频剪辑",
            "software_support": ["Final Cut Pro", "Premiere Pro"],
        },
        "M2": {
            "release_year": 2022,
            "typical_price_range": (6500, 11000),
            "video_editing_score": 85,
            "recommended_for": "1080p视频剪辑",
            "software_support": ["Final Cut Pro", "iMovie"],
        },

        # M1 系列 (2020-2021)
        "M1 Max": {
            "release_year": 2021,
            "typical_price_range": (10000, 18000),
            "video_editing_score": 92,
            "recommended_for": "专业4K视频剪辑",
            "software_support": ["Final Cut Pro", "DaVinci Resolve", "Premiere Pro"],
        },
        "M1 Pro": {
            "release_year": 2021,
            "typical_price_range": (7000, 13000),
            "video_editing_score": 88,
            "recommended_for": "4K视频剪辑",
            "software_support": ["Final Cut Pro", "Premiere Pro"],
        },
        "M1": {
            "release_year": 2020,
            "typical_price_range": (4500, 8500),
            "video_editing_score": 80,
            "recommended_for": "1080p视频剪辑、轻度4K",
            "software_support": ["Final Cut Pro", "iMovie"],
        },

        # Intel 系列 (2019-2020) - 性价比选择
        "Intel i9": {
            "release_year": 2019,
            "typical_price_range": (4500, 9500),
            "video_editing_score": 70,
            "recommended_for": "1080p视频剪辑（发热较大）",
            "software_support": ["Final Cut Pro", "DaVinci Resolve", "Premiere Pro"],
        },
    }
}
