#!/bin/bash

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 检查 .env 文件是否存在
if [ ! -f "$PROJECT_ROOT/config/.env" ]; then
    echo "错误: 配置文件 $PROJECT_ROOT/config/.env 不存在！"
    echo "请先复制 config/.env.example 为 config/.env 并修改配置。"
    exit 1
fi

echo "正在启动企业微信消息转发系统..."

# 启动服务端 (后台运行)
echo "启动服务端..."
cd "$PROJECT_ROOT/server"
python3 server.py > "$PROJECT_ROOT/server.log" 2>&1 &
SERVER_PID=$!
echo "服务端已启动，PID: $SERVER_PID，日志: server.log"

# 等待服务端启动
sleep 2

# 启动客户端 (后台运行)
echo "启动客户端..."
cd "$PROJECT_ROOT/client"
python3 client.py > "$PROJECT_ROOT/client.log" 2>&1 &
CLIENT_PID=$!
echo "客户端已启动，PID: $CLIENT_PID，日志: client.log"

echo "========================================="
echo "系统启动完成！"
echo "使用 'tail -f server.log' 查看服务端日志"
echo "使用 'tail -f client.log' 查看客户端日志"
echo "停止系统请运行: ./scripts/stop.sh"
echo "========================================="

# 保存 PID 以便停止脚本使用
echo $SERVER_PID > "$PROJECT_ROOT/server.pid"
echo $CLIENT_PID > "$PROJECT_ROOT/client.pid"
