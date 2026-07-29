@echo off
title Auto Mudae - Development Live Mode
echo ========================================================
echo   Auto Mudae - Live Development Environment (HMR + Reload)
echo ========================================================
echo.
echo 1. Launching Python Backend Daemon (Auto-Reload enabled)...
set WEBUI_RELOAD=1
set PYTHONPATH=%~dp0backend\src
start "Auto Mudae Python Backend (Auto-Reload)" cmd /k "set PYTHONPATH=%~dp0backend\src && set WEBUI_RELOAD=1 && python -m mudae.web.server"

echo 2. Launching Frontend Dev Server (Vite HMR)...
start "Auto Mudae WebUI Dev Server (Vite HMR)" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo ========================================================
echo   Live Dev Servers Started!
echo.
echo   - WebUI Frontend (HMR): http://localhost:5173
echo   - Backend API Daemon:   http://127.0.0.1:8765
echo ========================================================
echo.
pause
