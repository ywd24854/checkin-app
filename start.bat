@echo off
setlocal enableextensions
chcp 65001 >nul 2>nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem ---- optional user settings (create config.bat from config.bat.example) ----
if exist "config.bat" call "config.bat"

rem ---- locate Python 3 ----
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
  where python >nul 2>nul
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo ============================================================
  echo  [ERROR] Python 3 not found.
  echo.
  echo  1. Download Python 3.10+ :
  echo     https://www.python.org/downloads/windows/
  echo  2. During setup, CHECK "Add python.exe to PATH".
  echo  3. Then run start.bat again.
  echo ============================================================
  pause
  exit /b 1
)

rem ---- first run: make sure dependencies exist ----
%PY_CMD% -c "import fastapi, uvicorn, PIL, openpyxl, multipart" >nul 2>nul
if errorlevel 1 (
  echo [SETUP] First run detected, installing dependencies...
  %PY_CMD% -m pip install --upgrade pip >nul 2>nul
  %PY_CMD% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed.
    echo   - Check network / proxy, then retry: start.bat menu  then choose 2
    echo   - Offline server? See DEPLOY-Windows.md section "offline install".
    pause
    exit /b 1
  )
)

rem ---- launch (Chinese UI lives in launcher.py) ----
%PY_CMD% launcher.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [INFO] launcher exited with code %RC%
  pause
)
exit /b %RC%
