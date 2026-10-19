# 打包成免安装文件夹 dist\shuoshuo\（含 shuoshuo.exe 和 config.ini），拷到其他设备改好 config.ini 即可用
# 需要先建打包环境：py -3.10 -m venv %LOCALAPPDATA%\shuoshuo\build-venv，再 pip install pyinstaller -r requirements.txt
$ErrorActionPreference = "Stop"
$venv = "$env:LOCALAPPDATA\shuoshuo\build-venv"
$work = "$env:TEMP\shuoshuo-build"
New-Item -ItemType Directory -Force $work | Out-Null
& "$venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'$PSScriptRoot'); import shuoshuo as m; m.make_icon(m.COLORS['idle']).save(r'$work\shuoshuo.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])"
& "$venv\Scripts\pyinstaller.exe" --noconfirm --log-level WARN --noconsole --name shuoshuo --icon "$work\shuoshuo.ico" `
    --workpath $work --specpath $work --distpath "$PSScriptRoot\dist" "$PSScriptRoot\shuoshuo.py"
Copy-Item "$PSScriptRoot\config.example.ini" "$PSScriptRoot\dist\shuoshuo\config.ini"
Write-Output "done: $PSScriptRoot\dist\shuoshuo"
