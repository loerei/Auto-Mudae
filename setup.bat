@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "VENV_DIR=%ROOT%.venv"
set "VENV_PY=%VENV_DIR%\\Scripts\\python.exe"
set "PORTABLE_DIR=%ROOT%.portable"
set "PORTABLE_PY=%PORTABLE_DIR%\\python\\python.exe"
set "PORTABLE_SITE=%PORTABLE_DIR%\\site-packages"
set "PY_VER=3.11.9"
set "PY_ZIP=%PORTABLE_DIR%\\python-%PY_VER%-embed-amd64.zip"
set "PY_DIR=%PORTABLE_DIR%\\python"
set "GET_PIP=%PORTABLE_DIR%\\get-pip.py"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%ROOT%"

echo [setup] Root: %ROOT%

set "SYS_PY=python"
where python >nul 2>&1
if errorlevel 1 set "SYS_PY="

set "USE_PORTABLE=0"
if not defined SYS_PY (
    set "USE_PORTABLE=1"
)

set "VENV_OK=0"
if exist "%VENV_PY%" (
    set "VENV_HOME="
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"home =" "%VENV_DIR%\\pyvenv.cfg"`) do set "VENV_HOME=%%B"
    if defined VENV_HOME (
        set "VENV_HOME=!VENV_HOME:~1!"
        if exist "!VENV_HOME!\\python.exe" set "VENV_OK=1"
    )
)

if "%USE_PORTABLE%"=="1" (
    if not exist "%PORTABLE_PY%" (
        echo [setup] Python not found. Installing portable Python %PY_VER% ...
        if not exist "%PORTABLE_DIR%" mkdir "%PORTABLE_DIR%"
        powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip' -OutFile '%PY_ZIP%'"
        if errorlevel 1 (
            echo [error] Failed to download Python.
            goto :done
        )
        powershell -NoProfile -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%PY_DIR%' -Force"
        if errorlevel 1 (
            echo [error] Failed to extract Python.
            goto :done
        )
        if exist "%PY_DIR%\\python311._pth" (
            powershell -NoProfile -Command "(Get-Content '%PY_DIR%\\python311._pth') -replace '#import site','import site' | Set-Content '%PY_DIR%\\python311._pth'"
        )
        if not exist "%GET_PIP%" (
            powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%GET_PIP%'"
            if errorlevel 1 (
                echo [error] Failed to download get-pip.py.
                goto :done
            )
        )
        echo [setup] Installing pip ...
        "%PORTABLE_PY%" "%GET_PIP%" --no-warn-script-location
        if errorlevel 1 (
            echo [error] Failed to install pip.
            goto :done
        )
    ) else (
        echo [setup] Using existing portable Python.
    )
    set "PYTHON=%PORTABLE_PY%"
    if defined PYTHONPATH (
        set "PYTHONPATH=%PORTABLE_SITE%;%PYTHONPATH%"
    ) else (
        set "PYTHONPATH=%PORTABLE_SITE%"
    )
    if not exist "%PORTABLE_SITE%" mkdir "%PORTABLE_SITE%"
    echo [setup] Installing requirements into portable site-packages ...
    set "REQ_FILE="
    if exist "config\\requirements.txt" (
        set "REQ_FILE=config\\requirements.txt"
    ) else if exist "requirements.txt" (
        set "REQ_FILE=requirements.txt"
    )
    if defined REQ_FILE (
        "!PYTHON!" -m pip install -r "!REQ_FILE!" --target "%PORTABLE_SITE%" --no-warn-script-location
    ) else (
        echo [warn] No requirements file found. Installing core deps...
        "!PYTHON!" -m pip install discum requests colorama pynput --target "%PORTABLE_SITE%" --no-warn-script-location
    )
) else (
    if "%VENV_OK%"=="1" (
        set "PYTHON=%VENV_PY%"
        echo [setup] Using existing venv.
    ) else (
        if exist "%VENV_DIR%" (
            echo [setup] Existing venv is tied to a different Python. Recreate it?
            choice /m "Delete and recreate .venv"
            if errorlevel 2 goto :done
            rmdir /s /q "%VENV_DIR%"
        )
        echo [setup] Creating venv in .venv ...
        "!SYS_PY!" -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo [error] Failed to create venv.
            goto :done
        )
        set "PYTHON=%VENV_PY%"
    )
    echo [setup] Upgrading pip ...
    "!PYTHON!" -m pip install --upgrade pip

    set "REQ_FILE="
    if exist "config\requirements.txt" (
        set "REQ_FILE=config\requirements.txt"
    ) else if exist "requirements.txt" (
        set "REQ_FILE=requirements.txt"
    )
    if defined REQ_FILE (
        echo [setup] Installing requirements from !REQ_FILE! ...
        "!PYTHON!" -m pip install -r "!REQ_FILE!"
    ) else (
        echo [warn] No requirements file found. Installing core deps...
        "!PYTHON!" -m pip install discum requests colorama pynput
    )
)

if defined PYTHONPATH (
    set "PYTHONPATH=%ROOT%src;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%ROOT%src"
)

echo.
echo [setup] Setup complete.
choice /c 1234 /m "Run: [1] Bot  [2] $oh  [3] $oc  [4] Exit"
if errorlevel 4 goto :done
if errorlevel 3 goto :run_oc
if errorlevel 2 goto :run_oh
if errorlevel 1 goto :run_bot

:run_bot
if exist "run_bot.bat" (
    call "run_bot.bat"
) else (
    echo [warn] run_bot.bat not found. Launching mudae.cli.bot directly.
    "!PYTHON!" -m mudae.cli.bot
)
goto :done

:run_oh
if exist "run_oh.bat" (
    call "run_oh.bat"
) else (
    echo [warn] run_oh.bat not found. Launching mudae.cli.oh directly.
    "!PYTHON!" -m mudae.cli.oh
)
goto :done

:run_oc
if exist "run_oc.bat" (
    call "run_oc.bat"
) else (
    echo [warn] run_oc.bat not found. Launching mudae.cli.oc directly.
    "!PYTHON!" -m mudae.cli.oc
)
goto :done

:done
echo.
pause
