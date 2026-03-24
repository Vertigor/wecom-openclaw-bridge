"""
企业微信消息转发系统 - 服务端程序
功能：
  1. 建立与企业微信智能机器人的 WebSocket 长连接
  2. 接收企业微信推送的实时消息回调
  3. 将消息缓存到内存队列
  4. 提供 HTTP 接口供客户端轮询拉取未读消息
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────
from config import settings  # noqa: E402  (本地 config.py)

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
ws_running = False


def build_subscribe_request() -> str:
    """构造订阅请求，用于向企业微信进行身份认证。"""
    payload = {
        "cmd": "aibot_subscribe",
        "headers": {
            "req_id": str(uuid.uuid4()).replace("-", "")
        },
        "body": {
            "bot_id": settings.WECOM_BOT_ID,
            "secret": settings.WECOM_BOT_SECRET,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def build_ping_request() -> str:
    """构造心跳 ping 请求，用于保持长连接活跃。"""
    payload = {
        "cmd": "ping",
        "headers": {
            "req_id": str(uuid.uuid4()).replace("-", "")
        },
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_wecom_message(raw: str) -> Optional[Dict[str, Any]]:
    """
    解析企业微信 WebSocket 推送的原始消息。
    仅处理 aibot_msg_callback（用户消息）和 aibot_event_callback（事件）。
    返回标准化后的消息字典，或 None（表示无需处理的消息类型）。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("收到非 JSON 格式消息，已忽略: %s", raw[:200])
        return None

    cmd = data.get("cmd", "")
    body = data.get("body", {})

    if cmd == "aibot_msg_callback":
        msg_type = body.get("msgtype", "unknown")
        content = _extract_content(body, msg_type)
        return {
            "id": str(uuid.uuid4()),
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
            "msgid": body.get("msgid", ""),
            "cmd": cmd,
            "msgtype": "event",
            "content": f"[事件] {event_type}",
            "raw_body": body,
            "from_userid": body.get("from", {}).get("userid", ""),
            "chatid": body.get("chatid", ""),
            "chattype": body.get("chattype", "single"),
            "aibotid": body.get("aibotid", ""),
            "received_at": time.time(),
        }
    else:
        # ping 响应、subscribe 响应等无需缓存
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
    }
    extractor = extractors.get(msg_type)
    if extractor:
        return extractor(body)
    return f"[{msg_type}] 暂不支持的消息类型"


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
                ping_interval=None,   # 由应用层自行控制心跳
                ping_timeout=None,
                close_timeout=10,
            ) as ws:
                ws_connection = ws
                logger.info("WebSocket 连接成功，正在发送订阅请求...")

                # 发送订阅请求
                await ws.send(build_subscribe_request())
                subscribe_resp = await asyncio.wait_for(ws.recv(), timeout=15)
                resp_data = json.loads(subscribe_resp)
                if resp_data.get("errcode", -1) != 0:
                    logger.error(
                        "订阅失败，errcode=%s errmsg=%s",
                        resp_data.get("errcode"),
                        resp_data.get("errmsg"),
                    )
                    await asyncio.sleep(reconnect_delay)
                    continue

                logger.info("订阅成功，开始接收消息...")
                reconnect_delay = settings.WS_RECONNECT_DELAY_SECONDS  # 重置重连延迟

                # 启动心跳任务
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
            ws_connection = None

        if ws_running:
            await asyncio.sleep(reconnect_delay)
            # 指数退避，最长不超过 60 秒
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
    """应用生命周期管理：启动时开启 WebSocket 连接，关闭时停止。"""
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
    description="接收企业微信 WebSocket 消息并提供 HTTP 轮询接口",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# HTTP 接口
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
    limit: int = Query(default=100, ge=1, le=500, description="单次最多返回的消息数量"),
    clear: bool = Query(default=True, description="返回后是否清空缓存（默认清空）"),
):
    """
    拉取当前缓存中的所有未读消息。

    - `limit`：单次最多返回的消息数量，默认 100，最大 500。
    - `clear`：返回后是否立即清空缓存，默认为 True。
      若设置为 False，客户端需通过 `/api/messages/ack` 接口手动确认。
    """
    async with queue_lock:
        messages = list(message_queue)[:limit]
        if clear:
            # 只清除已返回的部分
            for _ in range(len(messages)):
                if message_queue:
                    message_queue.popleft()

    return MessageResponse(status="success", count=len(messages), data=messages)


@app.post("/api/messages/ack", summary="确认消息已处理（清空缓存）")
async def ack_messages():
    """
    手动清空消息缓存。当 `GET /api/messages?clear=false` 时，
    客户端处理完毕后调用此接口确认清空。
    """
    async with queue_lock:
        cleared = len(message_queue)
        message_queue.clear()
    return {"status": "success", "cleared": cleared}


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
    """简单的健康检查接口，用于负载均衡或监控探针。"""
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
