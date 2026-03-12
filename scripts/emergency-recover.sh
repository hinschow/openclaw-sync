#!/bin/bash
# OpenClaw 紧急恢复脚本
# 用法: bash /root/.openclaw/workspace/scripts/emergency-recover.sh
# 当 Telegram 不回复或系统卡死时使用

echo "🔧 OpenClaw 紧急恢复 $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1. 清理所有 session
echo "[1/4] 清理 sessions..."
find /root/.openclaw/agents/ -name "*.jsonl" -type f -mmin +60 -delete 2>/dev/null
for store in /root/.openclaw/agents/*/sessions/sessions.json; do
    [ -f "$store" ] && echo '{"sessions":[],"count":0}' > "$store"
done
echo "  ✅ Sessions 已清理"

# 2. 重启 gateway
echo "[2/4] 重启 gateway..."
systemctl --user restart openclaw-gateway.service 2>/dev/null || openclaw gateway restart 2>/dev/null
sleep 5

# 3. 验证
echo "[3/4] 验证..."
if pgrep -f "openclaw.*gateway" > /dev/null 2>&1; then
    echo "  ✅ Gateway 进程正常"
else
    echo "  ❌ Gateway 未启动，尝试手动启动..."
    nohup openclaw gateway --port 18789 > /tmp/openclaw-recovery.log 2>&1 &
    sleep 3
fi

if timeout 3 bash -c 'echo > /dev/tcp/127.0.0.1/18789' 2>/dev/null; then
    echo "  ✅ 端口 18789 响应正常"
else
    echo "  ❌ 端口无响应"
fi

# 4. 状态
echo "[4/4] 当前状态:"
openclaw status 2>&1 | head -20
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "恢复完成。如果 Telegram 仍无响应，请检查:"
echo "  1. openclaw gateway status"
echo "  2. tail -50 /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log"
echo "  3. openclaw doctor"
