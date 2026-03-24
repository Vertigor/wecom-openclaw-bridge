# 企业微信 → OpenClaw 消息转发系统

本系统实现了从**企业微信智能机器人**到 **OpenClaw Gateway** 的消息单向转发，完整覆盖以下消息流转链路：

```
企业微信 WebSocket → 服务端（WebSocket→HTTP + 缓存）→ 客户端（HTTP 轮询）→ OpenClaw（Channel）
```

---

## 目录结构

```
wecom-openclaw-bridge/
├── server/
│   ├── server.py        # 服务端主程序（WebSocket接收 + HTTP缓存接口）
│   └── config.py        # 服务端配置模块
├── client/
│   ├── client.py        # 客户端主程序（HTTP轮询 + OpenClaw转发）
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
| `WECOM_BOT_ID` | 企业微信智能机器人 BotID | 企业微信管理后台 → 应用 → 智能机器人 |
| `WECOM_BOT_SECRET` | 企业微信智能机器人 Secret | 同上 |
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw Gateway 认证 Token | `~/.openclaw/config.yml` 中的 `gateway.auth.token` |
| `OPENCLAW_BASE_URL` | OpenClaw Gateway 地址 | 默认 `http://127.0.0.1:18789` |

### 第三步：启动系统

```bash
./scripts/start.sh
```

### 第四步：查看日志

```bash
tail -f server.log   # 服务端日志（WebSocket连接状态、消息入队）
tail -f client.log   # 客户端日志（轮询结果、OpenClaw发送状态）
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
| `/api/messages/ack` | POST | 手动确认消息已处理，清空缓存 |
| `/api/status` | GET | 查询 WebSocket 连接状态和队列大小 |
| `/health` | GET | 健康检查（用于监控探针） |

**示例请求：**
```bash
# 拉取最新 50 条消息并清空缓存
curl http://127.0.0.1:8765/api/messages?limit=50&clear=true

# 查询服务状态
curl http://127.0.0.1:8765/api/status
```

---

## 完整配置说明

所有配置项均可通过 `config/.env` 文件或环境变量设置：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `WECOM_BOT_ID` | *(必填)* | 企业微信机器人 BotID |
| `WECOM_BOT_SECRET` | *(必填)* | 企业微信机器人 Secret |
| `SERVER_HOST` | `0.0.0.0` | 服务端监听地址 |
| `SERVER_PORT` | `8765` | 服务端监听端口 |
| `MAX_QUEUE_SIZE` | `1000` | 内存队列最大容量 |
| `WS_HEARTBEAT_INTERVAL_SECONDS` | `30` | WebSocket 心跳间隔（秒） |
| `WS_RECONNECT_DELAY_SECONDS` | `5` | 断线重连初始延迟（秒，支持指数退避） |
| `SERVER_BASE_URL` | `http://127.0.0.1:8765` | 客户端访问服务端的地址 |
| `POLL_INTERVAL_SECONDS` | `3` | 轮询间隔（秒） |
| `POLL_BATCH_SIZE` | `50` | 单次轮询最多拉取消息数 |
| `OPENCLAW_BASE_URL` | `http://127.0.0.1:18789` | OpenClaw Gateway 地址 |
| `OPENCLAW_GATEWAY_TOKEN` | *(必填)* | OpenClaw Gateway 认证 Token |
| `OPENCLAW_SESSION_KEY` | `main` | 目标会话 Key |
| `OPENCLAW_CHANNEL_HINT` | *(可选)* | Channel 上下文提示（如 `wecom`） |
| `HTTP_TIMEOUT_SECONDS` | `10` | HTTP 请求超时时间（秒） |

---

## 技术说明

### 企业微信 WebSocket 协议

服务端连接地址为 `wss://openws.work.weixin.qq.com`，连接后需发送 `aibot_subscribe` 命令进行身份认证，之后每 30 秒发送 `ping` 保持心跳。断线后支持指数退避自动重连（最长 60 秒）。

支持的消息类型：`text`、`markdown`、`image`、`file`、`video`、`voice`、`template_card`，以及 `enter_chat`、`disconnected_event` 等事件类型。

### OpenClaw 消息注入

客户端通过调用 OpenClaw Gateway 的 `POST /tools/invoke` 接口，使用 `chat_send` 工具将消息注入到指定会话。认证方式为 Bearer Token，对应 OpenClaw 配置中的 `gateway.auth.token`。

### 防重复发送

客户端维护一个已处理消息 ID 集合（上限 5000 条），确保同一消息不会被重复发送到 OpenClaw。超出上限时自动清理旧记录。

---

## 参考资料

- [企业微信智能机器人长连接文档](https://developer.work.weixin.qq.com/document/path/101463)
- [OpenClaw Tools Invoke API](https://docs.openclaw.ai/gateway/tools-invoke-http-api)
- [OpenClaw Gateway Protocol](https://docs.openclaw.ai/gateway/protocol)
