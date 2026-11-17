@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\StreamGrab-1.1.0\拾流下载器.exe" (
  start "" "dist\StreamGrab-1.1.0\拾流下载器.exe"
  exit /b
)
if exist "dist\StreamGrab\拾流下载器.exe" (
  start "" "dist\StreamGrab\拾流下载器.exe"
  exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "run.py"
  exit /b
)
echo 请先解压免安装发行包，双击其中的“拾流下载器.exe”。
pause
