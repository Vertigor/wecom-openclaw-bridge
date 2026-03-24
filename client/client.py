"""
企业微信消息转发系统 - 客户端程序
功能：
  1. 定时轮询服务端 HTTP 接口，检查是否有新消息
  2. 获取到新消息后，通过 OpenClaw Gateway HTTP API 发送给 OpenClaw
  3. 标记已处理消息，避免重复发送
"""

import json
import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Set

import httpx

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
logger = logging.getLogger("wecom-client")

# ─────────────────────────────────────────────
# 已处理消息 ID 集合（防重复发送）
# ─────────────────────────────────────────────
processed_ids: Set[str] = set()
MAX_PROCESSED_IDS = 5000  # 超出后自动清理最旧的一半，防止内存无限增长


def _add_processed_id(msg_id: str):
    """记录已处理的消息 ID，并在超出上限时清理旧记录。"""
    global processed_ids
    if len(processed_ids) >= MAX_PROCESSED_IDS:
        # 简单策略：清空一半（实际生产可用 LRU 或 TTL 机制）
        processed_ids = set(list(processed_ids)[MAX_PROCESSED_IDS // 2:])
    processed_ids.add(msg_id)


# ─────────────────────────────────────────────
# 服务端消息拉取
# ─────────────────────────────────────────────
def fetch_messages(client: httpx.Client) -> List[Dict[str, Any]]:
    """
    向服务端发起 HTTP GET 请求，拉取未读消息列表。
    返回消息列表，若请求失败则返回空列表。
    """
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
# OpenClaw 消息发送
# ─────────────────────────────────────────────
def build_openclaw_payload(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    将企业微信消息转换为 OpenClaw Gateway 接受的消息格式。

    OpenClaw Gateway 支持两种注入方式：
      1. /tools/invoke  → 调用内置工具（如 chat_send）
      2. /api/channels/<channel>/inject → 直接向指定 Channel 注入消息（部分版本支持）

    本实现默认使用 /tools/invoke 方式，通过 chat_send 工具注入消息。
    """
    content = message.get("content", "")
    from_user = message.get("from_userid", "unknown")
    msg_type = message.get("msgtype", "text")
    chattype = message.get("chattype", "single")

    # 构造发送给 OpenClaw 的消息文本（可根据需要自定义格式）
    if msg_type == "event":
        formatted_text = content
    else:
        formatted_text = (
            f"[来自企业微信]\n"
            f"发送人: {from_user}\n"
            f"会话类型: {'群聊' if chattype == 'group' else '单聊'}\n"
            f"消息类型: {msg_type}\n"
            f"内容: {content}"
        )

    # 使用 /tools/invoke 接口的 chat_send 工具
    return {
        "tool": "chat_send",
        "args": {
            "message": formatted_text,
        },
        "sessionKey": settings.OPENCLAW_SESSION_KEY,
    }


def send_to_openclaw(client: httpx.Client, message: Dict[str, Any]) -> bool:
    """
    将单条消息发送到 OpenClaw Gateway。
    返回 True 表示发送成功，False 表示失败。
    """
    msg_id = message.get("id", "")
    msgid = message.get("msgid", "")

    # 防重复：检查消息是否已处理
    dedup_key = msgid if msgid else msg_id
    if dedup_key and dedup_key in processed_ids:
        logger.debug("消息 %s 已处理，跳过", dedup_key)
        return True

    payload = build_openclaw_payload(message)
    url = f"{settings.OPENCLAW_BASE_URL}/tools/invoke"
    headers = {
        "Authorization": f"Bearer {settings.OPENCLAW_GATEWAY_TOKEN}",
        "Content-Type": "application/json",
    }
    # 可选：传递 Channel 上下文头
    if settings.OPENCLAW_CHANNEL_HINT:
        headers["x-openclaw-message-channel"] = settings.OPENCLAW_CHANNEL_HINT

    try:
        resp = client.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok", False):
            logger.info(
                "消息已成功发送到 OpenClaw [msgid=%s, from=%s]",
                msgid,
                message.get("from_userid", ""),
            )
            _add_processed_id(dedup_key)
            return True
        else:
            logger.error(
                "OpenClaw 返回失败结果: %s",
                json.dumps(result, ensure_ascii=False),
            )
            return False
    except httpx.ConnectError:
        logger.warning(
            "无法连接到 OpenClaw Gateway %s，请检查 Gateway 是否已启动",
            settings.OPENCLAW_BASE_URL,
        )
        return False
    except httpx.TimeoutException:
        logger.warning("发送消息到 OpenClaw 超时 [msgid=%s]", msgid)
        return False
    except httpx.HTTPStatusError as e:
        logger.error(
            "OpenClaw Gateway 返回错误状态码 %s [msgid=%s]: %s",
            e.response.status_code,
            msgid,
            e.response.text[:200],
        )
        return False
    except Exception as e:
        logger.exception("发送消息到 OpenClaw 时发生未知异常 [msgid=%s]: %s", msgid, e)
        return False


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
      2. 对每条消息调用 send_to_openclaw 发送
      3. 统计发送成功/失败数量并记录日志
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

            # 拉取消息
            messages = fetch_messages(http_client)

            # 逐条发送到 OpenClaw
            if messages:
                success_count = 0
                fail_count = 0
                for msg in messages:
                    if not _running:
                        break
                    ok = send_to_openclaw(http_client, msg)
                    if ok:
                        success_count += 1
                    else:
                        fail_count += 1
                        # 发送失败时短暂等待后继续（避免瞬间大量失败）
                        time.sleep(0.5)

                logger.info(
                    "本轮处理完成：成功 %d 条，失败 %d 条",
                    success_count,
                    fail_count,
                )

            # 等待下一个轮询周期（扣除本轮已消耗的时间）
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
