@echo off
REM RecTheWord v2 启动脚本：环境缺失时自动衔接 install.ps1
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo [start] 未检测到运行环境，正在自动安装...
    powershell -ExecutionPolicy Bypass -File install.ps1
    if errorlevel 1 (
        echo [start] 安装失败，请查看上方日志。
        pause
        exit /b 1
    )
)
.venv\Scripts\python.exe main.py
if errorlevel 1 pause
