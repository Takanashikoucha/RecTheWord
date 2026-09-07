@echo off
REM RecTheWord - one-shot Windows setup
REM Requires: Python 3.10-3.12 on PATH

echo === RecTheWord setup ===
python --version || (echo Python not found & exit /b 1)

echo [1/3] Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || exit /b 1

echo [2/3] Downloading FFmpeg (if not present)...
if not exist vendor\ffmpeg\ffmpeg.exe (
    python scripts\download_ffmpeg_windows.py || echo "FFmpeg download failed; install it manually."
) else (
    echo FFmpeg already present.
)

echo [3/3] Done.
echo Run the app with:  python main.py
pause
