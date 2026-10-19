@echo off
rem 无控制台窗口启动说说（客户端环境装在 %LOCALAPPDATA%\shuoshuo\venv）
start "" "%LOCALAPPDATA%\shuoshuo\venv\Scripts\pythonw.exe" "%~dp0shuoshuo.py"
