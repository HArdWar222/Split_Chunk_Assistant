@echo off
rem SplitChunkAssistant launcher (Windows).
rem ASCII only + CRLF line endings - do not add non-ASCII characters to this file,
rem cmd.exe reads batch files with the OEM codepage and UTF-8 text breaks parsing.
rem
rem Usage:
rem   run_studio.cmd                       default port 8790, opens browser
rem   run_studio.cmd --no-open             do not open browser
rem   run_studio.cmd --port 8801 --chunks-dir "D:\data\chunks_v2"
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo [x] Python not found in PATH.
  echo     Install Python 3.10+ from https://www.python.org/downloads/
  echo     and tick "Add python.exe to PATH" during install.
  pause
  exit /b 2
)

%PY% -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo [x] Python found but cannot run: %PY%
  pause
  exit /b 2
)

%PY% -m chunk_studio.server %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [x] Server exited with code %RC%
  echo     If the port is already taken, try: run_studio.cmd --port 8801
  pause
)
endlocal & exit /b %RC%