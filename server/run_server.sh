#!/usr/bin/env bash
# 启动识别服务：监听所有网卡，其他设备凭 .token 里的令牌访问
# 本机设置写在 server/.env（不进 git），例如：
#   SHUOSHUO_MODEL=/data/models/Qwen3-ASR-1.7B
#   SHUOSHUO_HISTORY=history   # 识别记录存到 server/history/，留空不存
#   SHUOSHUO_HISTORY_AUDIO=1   # 识别记录连音频一起存
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then . ./.env; fi
MODEL="${SHUOSHUO_MODEL:-Qwen/Qwen3-ASR-1.7B}"  # 不设就自动从 HuggingFace 下载
if [ ! -s .token ]; then
  umask 077
  .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(16))' > .token
fi
export SHUOSHUO_TOKEN="$(cat .token)"
ARGS=(--model "$MODEL" --host 0.0.0.0)
if [ -n "${SHUOSHUO_HISTORY:-}" ]; then ARGS+=(--history "$SHUOSHUO_HISTORY"); fi
if [ "${SHUOSHUO_HISTORY_AUDIO:-0}" = 1 ]; then ARGS+=(--history-audio); fi
exec .venv/bin/python server.py "${ARGS[@]}" "$@"
