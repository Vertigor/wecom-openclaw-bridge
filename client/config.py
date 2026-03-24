"""
客户端配置模块
优先从环境变量读取，其次从 .env 文件读取，最后使用默认值。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env 文件
_env_path = Path(__file__).parent.parent / "config" / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


class ClientSettings:
    """客户端配置项。"""

    # ── 服务端连接配置 ──────────────────────────────────────────────
    SERVER_BASE_URL: str = os.getenv("SERVER_BASE_URL", "http://127.0.0.1:8765")
    """服务端 HTTP 接口地址，默认本机 8765 端口。"""

    # ── 轮询配置 ────────────────────────────────────────────────────
    POLL_INTERVAL_SECONDS: float = float(os.getenv("POLL_INTERVAL_SECONDS", "3"))
    """轮询间隔（秒），默认每 3 秒拉取一次，可根据实时性需求调整。"""

    POLL_BATCH_SIZE: int = int(os.getenv("POLL_BATCH_SIZE", "50"))
    """单次轮询最多拉取的消息数量，默认 50 条。"""

    # ── OpenClaw Gateway 配置 ───────────────────────────────────────
    OPENCLAW_BASE_URL: str = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
    """OpenClaw Gateway 地址，默认本机 18789 端口（OpenClaw 默认端口）。"""

    OPENCLAW_GATEWAY_TOKEN: str = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
    """OpenClaw Gateway 认证 Token（对应 gateway.auth.token 配置项）。"""

    OPENCLAW_SESSION_KEY: str = os.getenv("OPENCLAW_SESSION_KEY", "main")
    """目标会话 Key，默认为 'main'（OpenClaw 主会话）。"""

    OPENCLAW_CHANNEL_HINT: str = os.getenv("OPENCLAW_CHANNEL_HINT", "")
    """可选：传递给 OpenClaw 的 Channel 上下文提示（如 'wecom'），用于 Group 路由策略。"""

    # ── HTTP 请求配置 ───────────────────────────────────────────────
    HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    """HTTP 请求超时时间（秒），默认 10 秒。"""


settings = ClientSettings()
