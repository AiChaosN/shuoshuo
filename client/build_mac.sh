#!/bin/bash
# macOS：打包成 dist/shuoshuo.app，拖进「应用程序」即可用（目标 Mac 不需要 Python）
# 配置文件在 ~/Library/Application Support/shuoshuo/config.ini，第一次运行时自动从模板生成
# 版本号：VERSION=0.2.0 ./build_mac.sh
# 每次重新打包后，系统设置里的辅助功能 / 输入监控授权可能要删掉重加
set -euo pipefail
cd "$(dirname "$0")"
VENV="$HOME/Library/Application Support/shuoshuo/venv"
WORK="${TMPDIR:-/tmp}/shuoshuo-build"
rm -rf "$WORK" && mkdir -p "$WORK/shuoshuo.iconset"
"$VENV/bin/pip" install -q pyinstaller
"$VENV/bin/python" -c "
import sys; sys.path.insert(0, '.')
import shuoshuo as m
img = m.make_icon(m.COLORS['idle'])
for s in (16, 32, 64, 128, 256, 512):
    img.resize((s, s)).save(f'$WORK/shuoshuo.iconset/icon_{s}x{s}.png')
    img.resize((s * 2, s * 2)).save(f'$WORK/shuoshuo.iconset/icon_{s}x{s}@2x.png')
"
iconutil -c icns "$WORK/shuoshuo.iconset" -o "$WORK/shuoshuo.icns"
"$VENV/bin/pyinstaller" --noconfirm --log-level WARN --windowed --name shuoshuo --icon "$WORK/shuoshuo.icns" \
    --osx-bundle-identifier io.github.aichaosn.shuoshuo --add-data "$(pwd)/config.example.ini:." \
    --workpath "$WORK" --specpath "$WORK" --distpath "$(pwd)/dist" "$(pwd)/shuoshuo.py"
PLIST=dist/shuoshuo.app/Contents/Info.plist
plutil -replace LSUIElement -bool true "$PLIST"
plutil -replace CFBundleShortVersionString -string "${VERSION:-0.0.0}" "$PLIST"
plutil -replace NSMicrophoneUsageDescription -string "按住热键说话时录音，发给你自己的识别服务" "$PLIST"
codesign --force --deep --sign - dist/shuoshuo.app
echo "done: $(pwd)/dist/shuoshuo.app"
