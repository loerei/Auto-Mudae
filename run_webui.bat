@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"

set "ROOT=%~dp0"
set "PORTABLE_DIR=%ROOT%.portable"
set "PORTABLE_PY=%PORTABLE_DIR%\python\python.exe"
set "PYTHON=python"
if exist "%PORTABLE_PY%" (
    set "PYTHON=%PORTABLE_PY%"
    if defined PYTHONPATH (
        set "PYTHONPATH=%PORTABLE_DIR%\site-packages;%PYTHONPATH%"
    ) else (
        set "PYTHONPATH=%PORTABLE_DIR%\site-packages"
    )
) else if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PYTHON=%ROOT%.venv\Scripts\python.exe"
)
if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%src"
)

set "UI_DIR=%ROOT%webui"
set "WEBUI_URL=http://127.0.0.1:8765/"
set "MUDAE_WEBUI_CLOSE_PARENT_CONSOLE=1"

cd /d "%ROOT%"

echo [webui] Checking Python dependencies...
"%PYTHON%" -c "import importlib.util, sys; mods=('fastapi','uvicorn','httpx'); missing=[name for name in mods if importlib.util.find_spec(name) is None]; sys.exit(1 if missing else 0)"
if errorlevel 1 (
    echo [webui] Installing Python dependencies...
    "%PYTHON%" -m pip install -r config\requirements.txt
    if errorlevel 1 (
        echo [webui] Failed to install Python dependencies.
        exit /b 1
    )
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [webui] npm was not found on PATH. Install Node.js and npm first.
    exit /b 1
)

if not exist "%UI_DIR%\node_modules" (
    echo [webui] Installing frontend dependencies...
    pushd "%UI_DIR%"
    call npm install
    if errorlevel 1 (
        popd
        echo [webui] npm install failed.
        exit /b 1
    )
    popd
)

echo [webui] Building frontend...
pushd "%UI_DIR%"
call npm run build
if errorlevel 1 (
    popd
    echo [webui] npm run build failed.
    exit /b 1
)
popd

start "" "%WEBUI_URL%"
echo [webui] Launching daemon on %WEBUI_URL%
"%PYTHON%" -m mudae.web.server
