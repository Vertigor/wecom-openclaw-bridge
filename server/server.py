"""
企业微信消息转发系统 - 服务端程序
功能：
  1. 建立与企业微信智能机器人的 WebSocket 长连接
  2. 接收企业微信推送的实时消息回调，缓存到内存队列
  3. 提供 HTTP 接口供客户端拉取未读消息
  4. 提供 HTTP 接口供客户端将 OpenClaw 处理结果回写，
     服务端再通过 WebSocket 将结果回复给企业微信用户
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import uvicorn
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────
from config import settings  # noqa: E402

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("wecom-server")

# ─────────────────────────────────────────────
# 消息缓存队列（线程安全的双端队列）
# ─────────────────────────────────────────────
message_queue: deque = deque(maxlen=settings.MAX_QUEUE_SIZE)
queue_lock = asyncio.Lock()

# ─────────────────────────────────────────────
# 企业微信 WebSocket 连接管理
# ─────────────────────────────────────────────
WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
ws_connection: Optional[websockets.WebSocketClientProtocol] = None
ws_lock = asyncio.Lock()
ws_running = False


# ─────────────────────────────────────────────
# 工具函数：构造 WebSocket 请求
# ─────────────────────────────────────────────
def build_subscribe_request() -> str:
    """构造订阅请求，用于向企业微信进行身份认证。"""
    return json.dumps({
        "cmd": "aibot_subscribe",
        "headers": {"req_id": str(uuid.uuid4()).replace("-", "")},
        "body": {
            "bot_id": settings.WECOM_BOT_ID,
            "secret": settings.WECOM_BOT_SECRET,
        },
    }, ensure_ascii=False)


def build_ping_request() -> str:
    """构造心跳 ping 请求。"""
    return json.dumps({
        "cmd": "ping",
        "headers": {"req_id": str(uuid.uuid4()).replace("-", "")},
    }, ensure_ascii=False)


def build_respond_msg(req_id: str, content: str, stream_id: str, finish: bool) -> str:
    """
    构造 aibot_respond_msg 请求（流式回复）。
    - req_id: 透传原始消息回调中的 req_id
    - content: 本次推送的文本内容
    - stream_id: 流式消息的唯一 ID（同一条回复保持不变）
    - finish: 是否为最后一帧
    """
    return json.dumps({
        "cmd": "aibot_respond_msg",
        "headers": {"req_id": req_id},
        "body": {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": finish,
                "content": content,
            },
        },
    }, ensure_ascii=False)


def build_respond_welcome_msg(req_id: str, content: str) -> str:
    """构造 aibot_respond_welcome_msg 请求（欢迎语回复）。"""
    return json.dumps({
        "cmd": "aibot_respond_welcome_msg",
        "headers": {"req_id": req_id},
        "body": {
            "msgtype": "markdown",
            "markdown": {"content": content},
        },
    }, ensure_ascii=False)


# ─────────────────────────────────────────────
# 消息解析
# ─────────────────────────────────────────────
def parse_wecom_message(raw: str) -> Optional[Dict[str, Any]]:
    """
    解析企业微信 WebSocket 推送的原始消息。
    仅处理 aibot_msg_callback 和 aibot_event_callback。
    返回标准化消息字典，或 None（表示无需处理）。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("收到非 JSON 格式消息，已忽略: %s", raw[:200])
        return None

    cmd = data.get("cmd", "")
    headers = data.get("headers", {})
    body = data.get("body", {})

    if cmd == "aibot_msg_callback":
        msg_type = body.get("msgtype", "unknown")
        content = _extract_content(body, msg_type)
        return {
            "id": str(uuid.uuid4()),
            "req_id": headers.get("req_id", ""),   # 回复时必须透传
            "msgid": body.get("msgid", ""),
            "cmd": cmd,
            "msgtype": msg_type,
            "content": content,
            "raw_body": body,
            "from_userid": body.get("from", {}).get("userid", ""),
            "chatid": body.get("chatid", ""),
            "chattype": body.get("chattype", "single"),
            "aibotid": body.get("aibotid", ""),
            "received_at": time.time(),
        }
    elif cmd == "aibot_event_callback":
        event_type = body.get("event", {}).get("eventtype", "unknown")
        return {
            "id": str(uuid.uuid4()),
            "req_id": headers.get("req_id", ""),
            "msgid": body.get("msgid", ""),
            "cmd": cmd,
            "msgtype": "event",
            "content": f"[事件] {event_type}",
            "event_type": event_type,
            "raw_body": body,
            "from_userid": body.get("from", {}).get("userid", ""),
            "chatid": body.get("chatid", ""),
            "chattype": body.get("chattype", "single"),
            "aibotid": body.get("aibotid", ""),
            "received_at": time.time(),
        }
    else:
        logger.debug("忽略非消息类型的 cmd: %s", cmd)
        return None


def _extract_content(body: Dict[str, Any], msg_type: str) -> str:
    """从消息体中提取可读内容。"""
    extractors = {
        "text": lambda b: b.get("text", {}).get("content", ""),
        "markdown": lambda b: b.get("markdown", {}).get("content", ""),
        "image": lambda b: f"[图片] url={b.get('image', {}).get('url', '')}",
        "file": lambda b: f"[文件] url={b.get('file', {}).get('url', '')}",
        "video": lambda b: f"[视频] url={b.get('video', {}).get('url', '')}",
        "voice": lambda b: f"[语音] url={b.get('voice', {}).get('url', '')}",
        "template_card": lambda b: f"[模板卡片] type={b.get('template_card', {}).get('card_type', '')}",
        "mixed": lambda b: f"[图文混排] {b.get('mixed', {})}",
    }
    extractor = extractors.get(msg_type)
    if extractor:
        return extractor(body)
    return f"[{msg_type}] 暂不支持的消息类型"


# ─────────────────────────────────────────────
# 企业微信 WebSocket 长连接主循环
# ─────────────────────────────────────────────
async def wecom_ws_client():
    """
    企业微信 WebSocket 长连接主循环。
    包含：连接建立、身份订阅、消息接收、心跳保持、断线重连。
    """
    global ws_connection, ws_running
    ws_running = True
    reconnect_delay = settings.WS_RECONNECT_DELAY_SECONDS

    while ws_running:
        try:
            logger.info("正在连接企业微信 WebSocket: %s", WECOM_WS_URL)
            async with websockets.connect(
                WECOM_WS_URL,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
            ) as ws:
                async with ws_lock:
                    ws_connection = ws

                logger.info("WebSocket 连接成功，正在发送订阅请求...")
                await ws.send(build_subscribe_request())
                subscribe_resp = await asyncio.wait_for(ws.recv(), timeout=15)
                resp_data = json.loads(subscribe_resp)

                if resp_data.get("errcode", -1) != 0:
                    logger.error(
                        "订阅失败，errcode=%s errmsg=%s",
                        resp_data.get("errcode"),
                        resp_data.get("errmsg"),
                    )
                    async with ws_lock:
                        ws_connection = None
                    await asyncio.sleep(reconnect_delay)
                    continue

                logger.info("订阅成功，开始接收消息...")
                reconnect_delay = settings.WS_RECONNECT_DELAY_SECONDS

                heartbeat_task = asyncio.create_task(_heartbeat(ws))
                try:
                    async for raw_message in ws:
                        logger.debug("收到原始消息: %s", raw_message[:300])
                        parsed = parse_wecom_message(raw_message)
                        if parsed:
                            async with queue_lock:
                                message_queue.append(parsed)
                            logger.info(
                                "消息已入队 [msgid=%s, type=%s, from=%s]",
                                parsed["msgid"],
                                parsed["msgtype"],
                                parsed["from_userid"],
                            )
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass

        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning("WebSocket 连接关闭: %s，%d 秒后重连...", e, reconnect_delay)
        except websockets.exceptions.InvalidHandshake as e:
            logger.error("WebSocket 握手失败: %s，%d 秒后重连...", e, reconnect_delay)
        except asyncio.TimeoutError:
            logger.warning("订阅响应超时，%d 秒后重连...", reconnect_delay)
        except Exception as e:
            logger.exception("WebSocket 连接异常: %s，%d 秒后重连...", e, reconnect_delay)
        finally:
            async with ws_lock:
                ws_connection = None

        if ws_running:
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


async def _heartbeat(ws: websockets.WebSocketClientProtocol):
    """定期发送心跳 ping，保持 WebSocket 连接活跃。"""
    while True:
        await asyncio.sleep(settings.WS_HEARTBEAT_INTERVAL_SECONDS)
        try:
            await ws.send(build_ping_request())
            logger.debug("心跳 ping 已发送")
        except Exception as e:
            logger.warning("心跳发送失败: %s", e)
            break


# ─────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global ws_running
    logger.info("服务端启动，初始化企业微信 WebSocket 连接...")
    ws_task = asyncio.create_task(wecom_ws_client())
    yield
    logger.info("服务端关闭，停止 WebSocket 连接...")
    ws_running = False
    ws_task.cancel()
    try:
        await ws_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="企业微信消息转发服务端",
    description="接收企业微信 WebSocket 消息并提供 HTTP 轮询/回复接口",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# HTTP 接口：消息拉取
# ─────────────────────────────────────────────
class MessageResponse(BaseModel):
    status: str
    count: int
    data: List[Dict[str, Any]]


class StatusResponse(BaseModel):
    status: str
    ws_connected: bool
    queue_size: int
    server_time: float


@app.get("/api/messages", response_model=MessageResponse, summary="拉取未读消息")
async def get_messages(
    limit: int = 100,
    clear: bool = True,
):
    """
    拉取当前缓存中的所有未读消息。

    - `limit`：单次最多返回的消息数量，默认 100。
    - `clear`：返回后是否立即清空缓存，默认 True。
    """
    async with queue_lock:
        messages = list(message_queue)[:limit]
        if clear:
            for _ in range(len(messages)):
                if message_queue:
                    message_queue.popleft()

    return MessageResponse(status="success", count=len(messages), data=messages)


@app.post("/api/messages/ack", summary="手动确认消息已处理（清空缓存）")
async def ack_messages():
    """手动清空消息缓存。"""
    async with queue_lock:
        cleared = len(message_queue)
        message_queue.clear()
    return {"status": "success", "cleared": cleared}


# ─────────────────────────────────────────────
# HTTP 接口：将 OpenClaw 结果回复给企业微信
# ─────────────────────────────────────────────
class ReplyRequest(BaseModel):
    req_id: str
    """原始消息回调中的 req_id，必须透传，用于关联回复与原始消息。"""

    content: str
    """要回复的文本内容（OpenClaw 处理结果）。"""

    is_welcome: bool = False
    """是否为欢迎语回复（enter_chat 事件触发时设为 True）。"""

    stream_id: Optional[str] = None
    """
    流式消息 ID（可选）。
    若为 None，服务端自动生成；同一条回复的多次分片需保持相同的 stream_id。
    """

    finish: bool = True
    """是否为流式消息的最后一帧，默认 True（一次性发完）。"""


class ReplyResponse(BaseModel):
    status: str
    message: str


@app.post("/api/reply", response_model=ReplyResponse, summary="将 OpenClaw 结果回复给企业微信")
async def reply_to_wecom(req: ReplyRequest):
    """
    接收客户端传来的 OpenClaw 处理结果，通过 WebSocket 回复给企业微信用户。

    **字段说明：**
    - `req_id`：从原始消息中透传的 req_id（必填）。
    - `content`：要发送的文本内容（必填）。
    - `is_welcome`：若为 True，使用 `aibot_respond_welcome_msg` 发送欢迎语。
    - `stream_id`：流式消息 ID，不填则自动生成。
    - `finish`：是否为最后一帧，默认 True。
    """
    async with ws_lock:
        current_ws = ws_connection

    if current_ws is None or current_ws.closed:
        raise HTTPException(
            status_code=503,
            detail="企业微信 WebSocket 连接不可用，请稍后重试",
        )

    try:
        if req.is_welcome:
            payload = build_respond_welcome_msg(req.req_id, req.content)
            cmd_desc = "aibot_respond_welcome_msg"
        else:
            sid = req.stream_id or str(uuid.uuid4()).replace("-", "")
            payload = build_respond_msg(req.req_id, req.content, sid, req.finish)
            cmd_desc = "aibot_respond_msg"

        async with ws_lock:
            await ws_connection.send(payload)

        logger.info(
            "已通过 WebSocket 回复企业微信 [cmd=%s, req_id=%s, finish=%s, len=%d]",
            cmd_desc,
            req.req_id,
            req.finish,
            len(req.content),
        )
        return ReplyResponse(status="success", message="回复已发送")

    except websockets.exceptions.ConnectionClosed as e:
        logger.error("发送回复时 WebSocket 连接已关闭: %s", e)
        raise HTTPException(status_code=503, detail=f"WebSocket 连接已关闭: {e}")
    except Exception as e:
        logger.exception("发送回复时发生异常: %s", e)
        raise HTTPException(status_code=500, detail=f"发送回复失败: {e}")


# ─────────────────────────────────────────────
# HTTP 接口：状态查询与健康检查
# ─────────────────────────────────────────────
@app.get("/api/status", response_model=StatusResponse, summary="查询服务状态")
async def get_status():
    """返回服务端当前状态，包括 WebSocket 连接状态和队列大小。"""
    return StatusResponse(
        status="running",
        ws_connected=(ws_connection is not None and not ws_connection.closed),
        queue_size=len(message_queue),
        server_time=time.time(),
    )


@app.get("/health", summary="健康检查")
async def health_check():
    return {"status": "ok"}


# ─────────────────────────────────────────────
# 程序入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=False,
        log_level="info",
    )
