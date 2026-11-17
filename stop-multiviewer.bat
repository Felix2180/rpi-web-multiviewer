@echo off
rem Stop rpi-web-multiviewer (all tile windows close with it)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*video_controller.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-Process mpv -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'mvzone-*' } | Stop-Process -Force"
echo Stopped.
