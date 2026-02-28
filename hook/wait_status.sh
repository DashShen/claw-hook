#!/usr/bin/env bash
# wait_status.sh - 等待 claw-hook 状态文件发生变化
#
# 用法：
#   bash wait_status.sh <initial_mtime> [timeout_seconds]
#
# 参数：
#   initial_mtime    上次读取时的文件 mtime（首次调用传 0）
#   timeout_seconds  本次最长等待秒数（默认 30）
#
# 返回（stdout）：
#   CHANGED          文件已更新，紧接着输出 status.json 全部内容
#   WAITING          超时未变化，第二行输出当前 mtime（传给下次调用）
#
# 示例（NanoBot 调用方式）：
#   第一次：bash wait_status.sh 0
#   后续：  bash wait_status.sh <上次返回的 mtime>
#   循环直到收到 CHANGED

set -euo pipefail

INITIAL_MTIME="${1:-0}"
TIMEOUT="${2:-30}"
STATUS_FILE="${CLAW_STATUS_FILE:-${HOME}/.claw-hook/status.json}"

get_mtime() {
    stat -c %Y "$STATUS_FILE" 2>/dev/null || echo 0
}

deadline=$(( $(date +%s) + TIMEOUT ))

while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 2
    [ -f "$STATUS_FILE" ] || continue

    current_mtime=$(get_mtime)
    if [ "$current_mtime" != "$INITIAL_MTIME" ]; then
        echo "CHANGED"
        cat "$STATUS_FILE"
        exit 0
    fi
done

# 超时，返回当前 mtime 供下次调用使用
echo "WAITING"
get_mtime
exit 0
