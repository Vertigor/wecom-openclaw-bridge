"""
企业微信消息转发系统 - 客户端程序
功能：
  1. 定时轮询服务端 HTTP 接口，检查是否有新消息
  2. 获取到新消息后，调用 OpenClaw Gateway POST /v1/responses 接口进行 AI 处理
  3. 将 OpenClaw 返回的处理结果，通过服务端 POST /api/reply 接口回复给企业微信用户
  4. 标记已处理消息，避免重复发送
"""

import json
import logging
import signal
import time
import uuid
from typing import Any, Dict, List, Optional, Set

import httpx

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
logger = logging.getLogger("wecom-client")

# ─────────────────────────────────────────────
# 已处理消息 ID 集合（防重复发送）
# ─────────────────────────────────────────────
processed_ids: Set[str] = set()
MAX_PROCESSED_IDS = 5000


def _add_processed_id(msg_id: str):
    """记录已处理的消息 ID，超出上限时自动清理旧记录。"""
    global processed_ids
    if len(processed_ids) >= MAX_PROCESSED_IDS:
        processed_ids = set(list(processed_ids)[MAX_PROCESSED_IDS // 2:])
    processed_ids.add(msg_id)


# ─────────────────────────────────────────────
# 服务端消息拉取
# ─────────────────────────────────────────────
def fetch_messages(client: httpx.Client) -> List[Dict[str, Any]]:
    """向服务端发起 HTTP GET 请求，拉取未读消息列表。"""
    url = f"{settings.SERVER_BASE_URL}/api/messages"
    params = {"limit": settings.POLL_BATCH_SIZE, "clear": "true"}
    try:
        resp = client.get(url, params=params, timeout=settings.HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("data", [])
        if messages:
            logger.info("从服务端拉取到 %d 条新消息", len(messages))
        return messages
    except httpx.ConnectError:
        logger.warning("无法连接到服务端 %s，请检查服务端是否已启动", settings.SERVER_BASE_URL)
        return []
    except httpx.TimeoutException:
        logger.warning("请求服务端超时")
        return []
    except httpx.HTTPStatusError as e:
        logger.error("服务端返回错误状态码: %s", e.response.status_code)
        return []
    except Exception as e:
        logger.exception("拉取消息时发生未知异常: %s", e)
        return []


# ─────────────────────────────────────────────
# 调用 OpenClaw /v1/responses
# ─────────────────────────────────────────────
def call_openclaw(client: httpx.Client, message: Dict[str, Any]) -> Optional[str]:
    """
    将消息内容发送到 OpenClaw Gateway POST /v1/responses 接口。
    返回 AI 处理后的文本结果，失败时返回 None。

    /v1/responses 遵循 OpenResponses API 规范：
    - 请求体：{"model": "openclaw", "input": "<用户消息>", "user": "<会话标识>"}
    - 响应体：{"output": [{"content": [{"type": "output_text", "text": "..."}]}]}
    """
    content = message.get("content", "")
    from_userid = message.get("from_userid", "unknown")
    chatid = message.get("chatid", "")
    chattype = message.get("chattype", "single")
    msgtype = message.get("msgtype", "text")

    # 事件类消息（如 enter_chat）不需要调用 AI，直接返回欢迎语
    if msgtype == "event":
        event_type = message.get("event_type", "")
        if event_type == "enter_chat":
            return settings.OPENCLAW_WELCOME_MESSAGE
        # 其他事件不回复
        return None

    # 构造发送给 OpenClaw 的用户消息
    # 使用 chatid（群聊）或 userid（单聊）作为稳定的 session 标识
    session_key = chatid if chattype == "group" and chatid else from_userid

    # 构造请求体（OpenResponses API 格式）
    payload = {
        "model": settings.OPENCLAW_MODEL,
        "input": content,
        "user": session_key,  # 用于 OpenClaw 内部的稳定会话路由
    }

    # 可选：附加系统指令
    if settings.OPENCLAW_SYSTEM_PROMPT:
        payload["instructions"] = settings.OPENCLAW_SYSTEM_PROMPT

    url = f"{settings.OPENCLAW_BASE_URL}/v1/responses"
    headers = {
        "Authorization": f"Bearer {settings.OPENCLAW_GATEWAY_TOKEN}",
        "Content-Type": "application/json",
    }
    if settings.OPENCLAW_CHANNEL_HINT:
        headers["x-openclaw-message-channel"] = settings.OPENCLAW_CHANNEL_HINT

    logger.info(
        "调用 OpenClaw /v1/responses [from=%s, session=%s, content_len=%d]",
        from_userid,
        session_key,
        len(content),
    )

    try:
        resp = client.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.OPENCLAW_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        result = resp.json()

        # 从 OpenResponses 格式的响应中提取文本
        ai_text = _extract_openclaw_text(result)
        if ai_text:
            logger.info(
                "OpenClaw 返回结果 [session=%s, len=%d]: %s...",
                session_key,
                len(ai_text),
                ai_text[:80],
            )
            return ai_text
        else:
            logger.warning("OpenClaw 响应中未找到文本内容: %s", json.dumps(result, ensure_ascii=False)[:300])
            return None

    except httpx.ConnectError:
        logger.warning("无法连接到 OpenClaw Gateway %s", settings.OPENCLAW_BASE_URL)
        return None
    except httpx.TimeoutException:
        logger.warning("调用 OpenClaw /v1/responses 超时（超过 %ds）", settings.OPENCLAW_TIMEOUT_SECONDS)
        return None
    except httpx.HTTPStatusError as e:
        logger.error(
            "OpenClaw 返回错误状态码 %s: %s",
            e.response.status_code,
            e.response.text[:300],
        )
        return None
    except Exception as e:
        logger.exception("调用 OpenClaw 时发生未知异常: %s", e)
        return None


def _extract_openclaw_text(result: Dict[str, Any]) -> Optional[str]:
    """
    从 OpenResponses API 响应中提取文本内容。

    响应结构示例：
    {
      "output": [
        {
          "type": "message",
          "content": [
            {"type": "output_text", "text": "AI 回复内容"}
          ]
        }
      ]
    }
    """
    output = result.get("output", [])
    texts = []
    for item in output:
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text = part.get("text", "").strip()
                    if text:
                        texts.append(text)
    if texts:
        return "\n".join(texts)

    # 兼容其他可能的响应格式
    if "text" in result:
        return result["text"].strip()
    if "content" in result:
        return str(result["content"]).strip()

    return None


# ─────────────────────────────────────────────
# 将 OpenClaw 结果回复给企业微信（通过服务端）
# ─────────────────────────────────────────────
def reply_to_wecom(
    client: httpx.Client,
    message: Dict[str, Any],
    ai_reply: str,
) -> bool:
    """
    调用服务端 POST /api/reply 接口，将 AI 回复发送回企业微信。
    服务端会通过 WebSocket 将内容回复给原始用户。
    返回 True 表示成功，False 表示失败。
    """
    req_id = message.get("req_id", "")
    msgid = message.get("msgid", "")
    is_welcome = (
        message.get("msgtype") == "event"
        and message.get("event_type") == "enter_chat"
    )

    if not req_id:
        logger.warning("消息缺少 req_id，无法回复 [msgid=%s]", msgid)
        return False

    url = f"{settings.SERVER_BASE_URL}/api/reply"
    payload = {
        "req_id": req_id,
        "content": ai_reply,
        "is_welcome": is_welcome,
        "stream_id": str(uuid.uuid4()).replace("-", ""),
        "finish": True,
    }

    try:
        resp = client.post(
            url,
            json=payload,
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") == "success":
            logger.info(
                "已成功回复企业微信用户 [msgid=%s, req_id=%s, reply_len=%d]",
                msgid,
                req_id,
                len(ai_reply),
            )
            return True
        else:
            logger.error("服务端回复接口返回失败: %s", result)
            return False
    except httpx.ConnectError:
        logger.warning("无法连接到服务端回复接口 %s", settings.SERVER_BASE_URL)
        return False
    except httpx.TimeoutException:
        logger.warning("调用服务端回复接口超时 [msgid=%s]", msgid)
        return False
    except httpx.HTTPStatusError as e:
        logger.error(
            "服务端回复接口返回错误状态码 %s [msgid=%s]: %s",
            e.response.status_code,
            msgid,
            e.response.text[:200],
        )
        return False
    except Exception as e:
        logger.exception("调用服务端回复接口时发生异常 [msgid=%s]: %s", msgid, e)
        return False


# ─────────────────────────────────────────────
# 处理单条消息的完整流程
# ─────────────────────────────────────────────
def process_message(client: httpx.Client, message: Dict[str, Any]) -> bool:
    """
    处理单条消息的完整流程：
      1. 防重复检查
      2. 调用 OpenClaw /v1/responses 获取 AI 回复
      3. 调用服务端 /api/reply 将结果回复给企业微信
    返回 True 表示处理成功，False 表示失败。
    """
    msg_id = message.get("id", "")
    msgid = message.get("msgid", "")
    dedup_key = msgid if msgid else msg_id

    # 防重复
    if dedup_key and dedup_key in processed_ids:
        logger.debug("消息 %s 已处理，跳过", dedup_key)
        return True

    # 调用 OpenClaw
    ai_reply = call_openclaw(client, message)
    if ai_reply is None:
        logger.warning("OpenClaw 未返回有效结果，跳过回复 [msgid=%s]", msgid)
        # 仍然标记为已处理，避免无限重试
        _add_processed_id(dedup_key)
        return False

    # 回复企业微信
    ok = reply_to_wecom(client, message, ai_reply)
    if ok:
        _add_processed_id(dedup_key)
    return ok


# ─────────────────────────────────────────────
# 主轮询循环
# ─────────────────────────────────────────────
_running = True


def _handle_signal(signum, frame):
    """处理 SIGINT / SIGTERM，优雅退出。"""
    global _running
    logger.info("收到退出信号 (%s)，正在停止客户端...", signum)
    _running = False


def run_poll_loop():
    """
    主轮询循环：
      1. 每隔 POLL_INTERVAL_SECONDS 秒向服务端拉取一次消息
      2. 对每条消息执行完整的处理流程（OpenClaw → 企业微信回复）
      3. 统计成功/失败数量并记录日志
    """
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "客户端启动，轮询间隔: %ds，服务端: %s，OpenClaw: %s",
        settings.POLL_INTERVAL_SECONDS,
        settings.SERVER_BASE_URL,
        settings.OPENCLAW_BASE_URL,
    )

    with httpx.Client() as http_client:
        while _running:
            poll_start = time.monotonic()

            messages = fetch_messages(http_client)

            if messages:
                success_count = 0
                fail_count = 0
                for msg in messages:
                    if not _running:
                        break
                    ok = process_message(http_client, msg)
                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1
                        time.sleep(0.5)

                logger.info(
                    "本轮处理完成：成功 %d 条，失败 %d 条",
                    success_count,
                    fail_count,
                )

            elapsed = time.monotonic() - poll_start
            sleep_time = max(0.0, settings.POLL_INTERVAL_SECONDS - elapsed)
            if _running and sleep_time > 0:
                time.sleep(sleep_time)

    logger.info("客户端已停止")


# ─────────────────────────────────────────────
# 程序入口
# ─────────────────────────────────────────────
if __name__ == "__main__":
    run_poll_loop()
