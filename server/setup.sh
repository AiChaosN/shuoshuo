#!/usr/bin/env bash
# 一次性安装服务端环境（需要 uv 和 NVIDIA 驱动）
set -euo pipefail
cd "$(dirname "$0")"
uv venv --python 3.11 --managed-python --clear .venv  # 用 uv 自己的 Python，不依赖 conda
uv pip install --python .venv/bin/python torch==2.10.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python --no-deps qwen-asr==0.0.6
[ -f hotwords.txt ] || cp hotwords.example.txt hotwords.txt
