@echo off
rem Start rpi-web-multiviewer with a console window (useful for troubleshooting)
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" src\video_controller.py %*
) else (
    py -3 src\video_controller.py %*
)
pause
