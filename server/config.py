"""
服务端配置模块
优先从环境变量读取，其次从 .env 文件读取，最后使用默认值。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
_env_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


class ServerSettings:
    """服务端配置项。"""

    # ── 企业微信机器人凭证 ──────────────────────────────────────────
    WECOM_BOT_ID: str = os.getenv("WECOM_BOT_ID", "YOUR_BOT_ID")
    """企业微信智能机器人的 BotID（在企业微信管理后台获取）。"""

    WECOM_BOT_SECRET: str = os.getenv("WECOM_BOT_SECRET", "YOUR_BOT_SECRET")
    """企业微信智能机器人的 Secret（在企业微信管理后台获取）。"""

    # ── HTTP 服务配置 ───────────────────────────────────────────────
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    """HTTP 服务监听地址，默认监听所有网卡。"""

    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8765"))
    """HTTP 服务监听端口，默认 8765。"""

    # ── 消息队列配置 ────────────────────────────────────────────────
    MAX_QUEUE_SIZE: int = int(os.getenv("MAX_QUEUE_SIZE", "1000"))
    """内存消息队列最大容量，超出后自动丢弃最旧的消息，默认 1000。"""

    # ── WebSocket 连接配置 ──────────────────────────────────────────
    WS_HEARTBEAT_INTERVAL_SECONDS: int = int(
        os.getenv("WS_HEARTBEAT_INTERVAL_SECONDS", "30")
    )
    """WebSocket 心跳发送间隔（秒），默认 30 秒。"""

    WS_RECONNECT_DELAY_SECONDS: int = int(
        os.getenv("WS_RECONNECT_DELAY_SECONDS", "5")
    )
    """WebSocket 断线后初始重连等待时间（秒），默认 5 秒（支持指数退避）。"""


settings = ServerSettings()
