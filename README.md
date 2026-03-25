# 企业微信 → OpenClaw 消息转发系统

本系统实现了**企业微信智能机器人**与 **OpenClaw Gateway** 的双向消息流转：接收企业微信用户消息，经 OpenClaw AI 处理后，将结果自动回复给原用户。

```
企业微信 WebSocket → 服务端（WebSocket→HTTP + 缓存）→ 客户端（HTTP 轮询）
→ OpenClaw（POST /v1/responses）→ 客户端（获取 AI 结果）
→ 服务端（POST /api/reply）→ 企业微信 WebSocket（aibot_respond_msg）→ 用户
```

---

## 目录结构

```
wecom-openclaw-bridge/
├── server/
│   ├── server.py        # 服务端主程序（WebSocket接收 + HTTP缓存/回复接口）
│   └── config.py        # 服务端配置模块
├── client/
│   ├── client.py        # 客户端主程序（HTTP轮询 + OpenClaw调用 + 回复）
│   └── config.py        # 客户端配置模块
├── config/
│   └── .env.example     # 配置文件模板（复制为 .env 后填写）
├── scripts/
│   ├── start.sh         # 一键启动脚本
│   └── stop.sh          # 一键停止脚本
├── requirements.txt     # Python 依赖列表
└── README.md            # 本文档
```

---

## 快速开始

### 第一步：安装依赖

```bash
pip install -r requirements.txt
```

### 第二步：配置

```bash
cp config/.env.example config/.env
```

编辑 `config/.env`，填入以下关键配置项：

| 配置项 | 说明 | 获取方式 |
|---|---|---|
| `WECOM_BOT_ID` | 企业微信智能机器人 BotID | 企业微信管理后台 → 应用 → 智能机器人 → 开启长连接 |
| `WECOM_BOT_SECRET` | 企业微信智能机器人长连接 Secret | 同上 |
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw Gateway 认证 Token | `~/.openclaw/config.yml` 中的 `gateway.auth.token` |
| `OPENCLAW_BASE_URL` | OpenClaw Gateway 地址 | 默认 `http://127.0.0.1:18789` |

### 第三步：启动系统

```bash
./scripts/start.sh
```

### 第四步：查看日志

```bash
tail -f server.log   # 服务端日志（WebSocket连接状态、消息入队）
tail -f client.log   # 客户端日志（轮询结果、OpenClaw调用、回复状态）
```

### 停止系统

```bash
./scripts/stop.sh
```

---

## 服务端 HTTP 接口

服务端默认监听 `http://0.0.0.0:8765`，提供以下接口：

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/messages` | GET | 拉取未读消息（`limit` 控制数量，`clear=true` 自动清空） |
| `/api/reply` | POST | 接收 AI 结果，通过 WebSocket 回复企业微信用户 |
| `/api/messages/ack` | POST | 手动确认消息已处理，清空缓存 |
| `/api/status` | GET | 查询 WebSocket 连接状态和队列大小 |
| `/health` | GET | 健康检查（用于监控探针） |

### POST /api/reply 请求体

```json
{
  "req_id": "原始消息回调中的 req_id（必填，用于关联回复）",
  "content": "要回复的文本内容（必填）",
  "is_welcome": false,
  "stream_id": "可选，流式消息 ID，不填则自动生成",
  "finish": true
}
```

**示例请求：**
```bash
# 拉取最新 50 条消息并清空缓存
curl "http://127.0.0.1:8765/api/messages?limit=50&clear=true"

# 查询服务状态
curl http://127.0.0.1:8765/api/status
```

---

## OpenClaw 接口说明

客户端调用 `POST /v1/responses`（OpenResponses API 规范）：

```bash
POST http://<OPENCLAW_BASE_URL>/v1/responses
Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>
Content-Type: application/json

{
  "model": "openclaw",
  "input": "用户消息内容",
  "user": "会话标识（userid 或 chatid）",
  "instructions": "可选的系统指令"
}
```

响应中通过 `output[].content[].text` 提取 AI 回复文本：

```json
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
```

---

## 完整配置说明

所有配置项均可通过 `config/.env` 文件或环境变量设置：

### 服务端配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `WECOM_BOT_ID` | *(必填)* | 企业微信机器人 BotID |
| `WECOM_BOT_SECRET` | *(必填)* | 企业微信机器人长连接 Secret |
| `SERVER_HOST` | `0.0.0.0` | 服务端监听地址 |
| `SERVER_PORT` | `8765` | 服务端监听端口 |
| `MAX_QUEUE_SIZE` | `1000` | 内存队列最大容量 |
| `WS_HEARTBEAT_INTERVAL_SECONDS` | `30` | WebSocket 心跳间隔（秒） |
| `WS_RECONNECT_DELAY_SECONDS` | `5` | 断线重连初始延迟（秒，支持指数退避） |

### 客户端配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `SERVER_BASE_URL` | `http://127.0.0.1:8765` | 客户端访问服务端的地址 |
| `POLL_INTERVAL_SECONDS` | `3` | 轮询间隔（秒） |
| `POLL_BATCH_SIZE` | `50` | 单次轮询最多拉取消息数 |
| `HTTP_TIMEOUT_SECONDS` | `10` | HTTP 请求超时时间（秒） |
| `OPENCLAW_BASE_URL` | `http://127.0.0.1:18789` | OpenClaw Gateway 地址 |
| `OPENCLAW_GATEWAY_TOKEN` | *(必填)* | OpenClaw Gateway Bearer Token |
| `OPENCLAW_MODEL` | `openclaw` | `/v1/responses` 的 model 字段值 |
| `OPENCLAW_SYSTEM_PROMPT` | *(可选)* | 系统指令（instructions 字段） |
| `OPENCLAW_CHANNEL_HINT` | *(可选)* | Channel 标识（请求头 `x-openclaw-message-channel`） |
| `OPENCLAW_TIMEOUT_SECONDS` | `60` | OpenClaw 调用超时时间（秒） |
| `OPENCLAW_WELCOME_MESSAGE` | `您好！...` | 用户进入会话时的欢迎语 |

---

## 技术说明

### 企业微信 WebSocket 协议

服务端连接地址为 `wss://openws.work.weixin.qq.com`，连接后需发送 `aibot_subscribe` 命令进行身份认证，之后每 30 秒发送 `ping` 保持心跳。断线后支持指数退避自动重连（最长 60 秒）。

回复消息通过 `aibot_respond_msg`（流式消息格式）发送，`req_id` 必须透传自原始消息回调，用于企业微信服务器关联回复与原始消息。欢迎语通过 `aibot_respond_welcome_msg` 发送。

### 消息类型支持

| 消息类型 | 说明 | 是否转发 OpenClaw |
|---|---|---|
| `text` | 文本消息 | 是 |
| `markdown` | Markdown 消息 | 是 |
| `image` | 图片（仅单聊） | 是（传递图片 URL） |
| `file` | 文件（仅单聊） | 是（传递文件 URL） |
| `video` | 视频（仅单聊） | 是（传递视频 URL） |
| `voice` | 语音（仅单聊） | 是（传递语音 URL） |
| `event/enter_chat` | 用户进入会话 | 否（直接回复欢迎语） |
| `event/其他` | 其他事件 | 否 |

### 防重复发送

客户端维护一个已处理消息 ID 集合（上限 5000 条），确保同一消息不会被重复发送到 OpenClaw。超出上限时自动清理旧记录。

---

## 企业微信后台配置步骤

1. 登录企业微信管理后台，进入「应用管理」→「智能机器人」
2. 创建或选择已有机器人，进入配置页面
3. 开启「API 模式」，选择「**长连接**」方式（非 Webhook）
4. 记录 **BotID** 和 **Secret**，填入 `config/.env`

> **注意**：长连接模式与 Webhook 模式互斥，切换后原有回调地址将失效。

---

## 参考资料

- [企业微信智能机器人长连接文档](https://developer.work.weixin.qq.com/document/path/101463)
- [OpenClaw OpenResponses API](https://docs.openclaw.ai/gateway/openresponses-http-api)
