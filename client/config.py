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

    HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "10"))
    """HTTP 请求超时时间（秒），默认 10 秒。"""

    # ── OpenClaw Gateway 配置 ───────────────────────────────────────
    OPENCLAW_BASE_URL: str = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
    """OpenClaw Gateway 地址，默认本机 18789 端口（OpenClaw 默认端口）。"""

    OPENCLAW_GATEWAY_TOKEN: str = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
    """OpenClaw Gateway 认证 Token（Bearer Token），对应 gateway.auth.token 配置项。"""

    OPENCLAW_MODEL: str = os.getenv("OPENCLAW_MODEL", "openclaw")
    """
    发送给 POST /v1/responses 的 model 字段值。
    通常填写 OpenClaw 配置的 model 名称，默认为 'openclaw'。
    """

    OPENCLAW_SYSTEM_PROMPT: str = os.getenv("OPENCLAW_SYSTEM_PROMPT", "")
    """
    可选的系统指令（instructions 字段），将作为 AI 的角色设定。
    为空则不附加，OpenClaw 使用自身默认的 system prompt。
    """

    OPENCLAW_CHANNEL_HINT: str = os.getenv("OPENCLAW_CHANNEL_HINT", "")
    """
    可选的 Channel 标识，将作为 x-openclaw-message-channel 请求头发送，
    用于 OpenClaw 内部路由到指定 Channel。为空则不附加。
    """

    OPENCLAW_TIMEOUT_SECONDS: float = float(os.getenv("OPENCLAW_TIMEOUT_SECONDS", "60"))
    """
    调用 OpenClaw /v1/responses 的超时时间（秒）。
    AI 推理可能耗时较长，建议设置为 60 秒或以上。
    """

    OPENCLAW_WELCOME_MESSAGE: str = os.getenv(
        "OPENCLAW_WELCOME_MESSAGE",
        "您好！我是 AI 智能助手，有什么可以帮您的吗？",
    )
    """
    用户首次进入会话时（enter_chat 事件）发送的欢迎语。
    该欢迎语不经过 OpenClaw 处理，直接回复给用户。
    """


settings = ClientSettings()
