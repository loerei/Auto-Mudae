@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "ROOT=%~dp0"
set "PORTABLE_DIR=%ROOT%.portable"
set "PORTABLE_PY=%PORTABLE_DIR%\python\python.exe"
set "PYTHON=python"
set "PYTHONDONTWRITEBYTECODE=1"
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

cd /d "%ROOT%"
"%PYTHON%" -m mudae.cli.oh
pause
