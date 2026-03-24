#!/bin/bash

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "正在停止企业微信消息转发系统..."

# 停止客户端
if [ -f "$PROJECT_ROOT/client.pid" ]; then
    CLIENT_PID=$(cat "$PROJECT_ROOT/client.pid")
    if ps -p $CLIENT_PID > /dev/null; then
        echo "停止客户端 (PID: $CLIENT_PID)..."
        kill $CLIENT_PID
    else
        echo "客户端进程已不存在。"
    fi
    rm "$PROJECT_ROOT/client.pid"
else
    echo "未找到客户端 PID 文件。"
fi

# 停止服务端
if [ -f "$PROJECT_ROOT/server.pid" ]; then
    SERVER_PID=$(cat "$PROJECT_ROOT/server.pid")
    if ps -p $SERVER_PID > /dev/null; then
        echo "停止服务端 (PID: $SERVER_PID)..."
        kill $SERVER_PID
    else
        echo "服务端进程已不存在。"
    fi
    rm "$PROJECT_ROOT/server.pid"
else
    echo "未找到服务端 PID 文件。"
fi

echo "系统已停止。"
