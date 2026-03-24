# 企业微信到 OpenClaw 消息转发系统

本系统用于将企业微信智能机器人的实时消息，通过 WebSocket 接收并缓存，然后由客户端定时轮询并转发至 OpenClaw Gateway。

## 1. 系统架构

系统分为两个独立的 Python 程序：
1. **服务端 (`server/server.py`)**：
   - 与企业微信建立 WebSocket 长连接。
   - 接收实时消息并存入内存队列。
   - 提供 HTTP 接口供客户端拉取消息。
2. **客户端 (`client/client.py`)**：
   - 定时轮询服务端的 HTTP 接口获取新消息。
   - 将消息格式化后，通过 HTTP POST 调用 OpenClaw Gateway 的 `/tools/invoke` 接口（使用 `chat_send` 工具）注入消息。

## 2. 环境要求

- Python 3.11+
- 依赖包：`websockets`, `fastapi`, `uvicorn`, `httpx`, `schedule`, `pydantic`, `python-dotenv`

安装依赖：
```bash
pip install -r requirements.txt
```

## 3. 配置说明

1. 复制配置文件模板：
   ```bash
   cp config/.env.example config/.env
   ```
2. 编辑 `config/.env` 文件，填入以下关键信息：
   - `WECOM_BOT_ID`: 企业微信智能机器人的 BotID
   - `WECOM_BOT_SECRET`: 企业微信智能机器人的 Secret
   - `OPENCLAW_GATEWAY_TOKEN`: OpenClaw Gateway 的认证 Token

## 4. 运行系统

系统提供了便捷的启动和停止脚本。

**启动系统：**
```bash
./scripts/start.sh
```
启动后，服务端和客户端将在后台运行，并分别输出日志到 `server.log` 和 `client.log`。

**查看日志：**
```bash
tail -f server.log
tail -f client.log
```

**停止系统：**
```bash
./scripts/stop.sh
```

## 5. 接口说明 (服务端)

- `GET /api/messages?limit=100&clear=true`：拉取未读消息。
- `POST /api/messages/ack`：手动确认消息已处理（清空缓存）。
- `GET /api/status`：查看服务端运行状态和队列大小。
- `GET /health`：健康检查。
