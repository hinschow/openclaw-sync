#!/bin/bash
# 系统自动清理脚本
# 用法: bash /root/.openclaw/workspace/scripts/cleanup.sh

echo "=== 系统清理 $(date -u '+%Y-%m-%d %H:%M UTC') ==="

# 1. 清理超过7天的 session 日志
echo ""
echo "[1] 清理 session 日志 (>7天)..."
BEFORE=$(du -sh /root/.openclaw/agents/main/sessions/ 2>/dev/null | cut -f1)
find /root/.openclaw/agents/main/sessions/ -name "*.jsonl" -mtime +7 -delete 2>/dev/null
AFTER=$(du -sh /root/.openclaw/agents/main/sessions/ 2>/dev/null | cut -f1)
echo "  Session 日志: ${BEFORE:-0} → ${AFTER:-0}"

# 2. 清理 Python __pycache__
echo ""
echo "[2] 清理 __pycache__..."
CACHE_COUNT=$(find /root/.openclaw/workspace -type d -name "__pycache__" 2>/dev/null | wc -l)
find /root/.openclaw/workspace -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
echo "  清理了 ${CACHE_COUNT} 个 __pycache__ 目录"

# 3. 报告磁盘和内存使用
echo ""
echo "[3] 系统状态:"
echo "--- 磁盘 ---"
df -h /
echo ""
echo "--- 内存 ---"
free -h
echo ""
echo "--- data 目录 ---"
du -sh /root/.openclaw/workspace/polymarket-tracker/data/
echo ""
echo "=== 清理完成 ==="
