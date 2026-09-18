# RecTheWord v2 安装脚本（uv 加速；模型只走 ModelScope）
# 用法：powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

Write-Host "==> [1/4] 检查 Python (uv)" -ForegroundColor Cyan
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "    未找到 uv，下载安装..."
    New-Item -ItemType Directory -Force tools | Out-Null
    Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile "tools\_uv_install.ps1"
    powershell -ExecutionPolicy Bypass -File "tools\_uv_install.ps1"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
}
Write-Host "    uv OK"

Write-Host "==> [2/4] 创建虚拟环境 .venv (Python 3.12)" -ForegroundColor Cyan
& $uv.Source venv --python 3.12 .venv | Out-Null

Write-Host "==> [3/4] 安装依赖 (uv pip，比 pip 快约 10 倍)" -ForegroundColor Cyan
& $uv.Source pip install --python .venv\Scripts\python.exe -r requirements.txt
& $uv.Source pip install --python .venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cpu

Write-Host "==> [4/4] 预取模型 (ModelScope)" -ForegroundColor Cyan
New-Item -ItemType Directory -Force models | Out-Null
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from rtw.core.model_manager import ModelManager; from rtw.core.events import EventBus; mm=ModelManager(__import__('pathlib').Path('models'), EventBus()); [mm.ensure(k) for k in ['qwen3_asr','silero_vad']]; print('models ready')"

Write-Host "`n安装完成。运行 start.bat 启动。" -ForegroundColor Green
