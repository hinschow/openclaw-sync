#!/bin/bash
# Session 自动清理 + 自愈脚本 v2
# 覆盖 main agent sessions + isolated cron sessions
# 当 session 数量过高或 gateway 异常时自动修复

set -euo pipefail

MAIN_SESSION_DIR="/root/.openclaw/agents/main/sessions"
MAX_SESSIONS=15
WARN_THRESHOLD=25
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"
CLEANED=0
ALERTS=""

# ━━━ 1. 清理 main agent sessions ━━━
if [ -d "$MAIN_SESSION_DIR" ]; then
    count=$(find "$MAIN_SESSION_DIR" -name "*.jsonl" -type f 2>/dev/null | wc -l)
    echo "$LOG_PREFIX Main session JSONL count: $count"

    if [ "$count" -gt "$MAX_SESSIONS" ]; then
        echo "$LOG_PREFIX ⚠️ Main sessions ($count) > max ($MAX_SESSIONS), pruning..."
        find "$MAIN_SESSION_DIR" -name "*.jsonl" -type f -printf '%T@ %p\n' | \
            sort -n | head -n -"$MAX_SESSIONS" | awk '{print $2}' | \
            while read f; do
                echo "  Removing: $(basename "$f")"
                rm -f "$f"
                CLEANED=$((CLEANED + 1))
            done
        remaining=$(find "$MAIN_SESSION_DIR" -name "*.jsonl" -type f 2>/dev/null | wc -l)
        ALERTS="${ALERTS}Main sessions pruned: ${count} → ${remaining}\n"
    fi
fi

# ━━━ 2. 清理 isolated/cron sessions (所有 agent 目录) ━━━
for agent_dir in /root/.openclaw/agents/*/; do
    agent_name=$(basename "$agent_dir")
    sess_dir="${agent_dir}sessions"
    [ -d "$sess_dir" ] || continue

    count=$(find "$sess_dir" -name "*.jsonl" -type f 2>/dev/null | wc -l)
    if [ "$count" -gt "$WARN_THRESHOLD" ]; then
        echo "$LOG_PREFIX ⚠️ Agent '$agent_name' sessions ($count) > threshold ($WARN_THRESHOLD), pruning..."
        find "$sess_dir" -name "*.jsonl" -type f -printf '%T@ %p\n' | \
            sort -n | head -n -"$MAX_SESSIONS" | awk '{print $2}' | \
            while read f; do
                rm -f "$f"
            done
        remaining=$(find "$sess_dir" -name "*.jsonl" -type f 2>/dev/null | wc -l)
        ALERTS="${ALERTS}Agent '$agent_name' sessions pruned: ${count} → ${remaining}\n"
    fi
done

# ━━━ 3. 清理超大 session 文件 (>5MB) ━━━
large_files=$(find /root/.openclaw/agents/ -name "*.jsonl" -type f -size +5M 2>/dev/null)
if [ -n "$large_files" ]; then
    echo "$LOG_PREFIX ⚠️ Found oversized session files (>5MB):"
    echo "$large_files" | while read f; do
        size=$(du -h "$f" | cut -f1)
        echo "  $size $(basename "$f")"
        rm -f "$f"
    done
    ALERTS="${ALERTS}Removed oversized session files (>5MB)\n"
fi

# ━━━ 4. 清理过期 session store entries ━━━
for store in /root/.openclaw/agents/*/sessions/sessions.json; do
    [ -f "$store" ] || continue
    agent_dir=$(dirname "$(dirname "$store")")
    sess_dir="${agent_dir}/sessions"

    # 获取 store 中引用的 session IDs
    if command -v python3 &>/dev/null; then
        python3 -c "
import json, os, sys
store_path = '$store'
sess_dir = '$sess_dir'
try:
    with open(store_path) as f:
        data = json.load(f)
    sessions = data.get('sessions', [])
    # 清理超过48小时未更新的 isolated sessions
    import time
    now = time.time() * 1000
    cleaned = []
    kept = []
    for s in sessions:
        age_hours = (now - s.get('updatedAt', now)) / 3600000
        if s.get('kind') != 'direct' and age_hours > 48:
            cleaned.append(s)
            sid = s.get('sessionId', '')
            jsonl = os.path.join(sess_dir, sid + '.jsonl')
            if os.path.exists(jsonl):
                os.remove(jsonl)
        else:
            kept.append(s)
    if cleaned:
        data['sessions'] = kept
        data['count'] = len(kept)
        with open(store_path, 'w') as f:
            json.dump(data, f, indent=2)
        print(f'Cleaned {len(cleaned)} stale sessions from store')
except Exception as e:
    print(f'Store cleanup error: {e}', file=sys.stderr)
" 2>&1
    fi
done

# ━━━ 5. Gateway 健康检查 + 自愈 ━━━
echo "$LOG_PREFIX Checking gateway health..."
GW_OK=true

# 检查进程是否存在
if ! pgrep -f "openclaw.*gateway" > /dev/null 2>&1; then
    echo "$LOG_PREFIX ❌ Gateway process not found!"
    ALERTS="${ALERTS}❌ Gateway process not found, attempting restart\n"
    GW_OK=false
fi

# 检查端口是否响应
if $GW_OK; then
    if ! timeout 5 bash -c 'echo > /dev/tcp/127.0.0.1/18789' 2>/dev/null; then
        echo "$LOG_PREFIX ❌ Gateway port 18789 not responding!"
        ALERTS="${ALERTS}❌ Gateway port not responding, attempting restart\n"
        GW_OK=false
    fi
fi

# 如果不健康，尝试重启
if ! $GW_OK; then
    echo "$LOG_PREFIX 🔄 Attempting gateway restart..."
    systemctl --user restart openclaw-gateway.service 2>/dev/null || \
        openclaw gateway restart 2>/dev/null || \
        echo "$LOG_PREFIX ❌ Failed to restart gateway"
    sleep 5
    if pgrep -f "openclaw.*gateway" > /dev/null 2>&1; then
        ALERTS="${ALERTS}✅ Gateway restarted successfully\n"
    else
        ALERTS="${ALERTS}❌ Gateway restart failed, manual intervention needed\n"
    fi
fi

# ━━━ 6. 检查内存使用 ━━━
if command -v pgrep &>/dev/null; then
    gw_pid=$(pgrep -f "openclaw.*gateway" | head -1)
    if [ -n "$gw_pid" ]; then
        mem_mb=$(ps -o rss= -p "$gw_pid" 2>/dev/null | awk '{printf "%.0f", $1/1024}')
        echo "$LOG_PREFIX Gateway memory: ${mem_mb}MB (pid: $gw_pid)"
        if [ "${mem_mb:-0}" -gt 1024 ]; then
            ALERTS="${ALERTS}⚠️ Gateway memory high: ${mem_mb}MB, restarting\n"
            systemctl --user restart openclaw-gateway.service 2>/dev/null || true
        fi
    fi
fi

# ━━━ 输出结果 ━━━
if [ -n "$ALERTS" ]; then
    echo ""
    echo "━━━ ALERTS ━━━"
    echo -e "$ALERTS"
    echo "$ALERTS"  # 供 cron agent 读取并发送
else
    echo "$LOG_PREFIX ✅ All checks passed, no action needed"
fi
