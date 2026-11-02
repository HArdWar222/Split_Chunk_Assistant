@echo off
rem SplitChunkAssistant 启动脚本（Windows）
rem 用法：run_studio.cmd            默认端口 8790，自动打开浏览器
rem       run_studio.cmd --no-open  不自动打开浏览器
rem       run_studio.cmd --port 8801 --chunks-dir "D:\data\chunks_v2"
setlocal
cd /d "%~dp0"

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 找不到 python，请先安装 Python 3.10+ 并加入 PATH
  exit /b 2
)

python -m chunk_studio.server %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [x] 服务异常退出，退出码 %RC%
)
endlocal & exit /b %RC%
