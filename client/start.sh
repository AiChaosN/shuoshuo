#!/bin/bash
# macOS：后台启动说说（客户端环境装在 ~/Library/Application Support/shuoshuo/venv）
# 权限（辅助功能、输入监控、麦克风）会记在启动它的终端 App 上
cd "$(dirname "$0")"
nohup "$HOME/Library/Application Support/shuoshuo/venv/bin/python" shuoshuo.py >/dev/null 2>&1 &
