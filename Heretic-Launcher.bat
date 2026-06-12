@echo off
rem ---------------------------------------------------------------------
rem Heretic-Launcher.bat - double-click entry point for the Heretic GUI.
rem
rem Finds a way to run scripts\launcher.py (stdlib-only tkinter app):
rem   1. uv on PATH                 -> uv run --no-project python
rem   2. uv in default install dir  -> same, with full path
rem   3. Python launcher (pyw/py)   -> run directly
rem   4. python on PATH             -> run directly
rem   5. nothing found              -> offer to install uv, then run
rem ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if %errorlevel%==0 (
    start "Heretic Launcher" /min cmd /c "uv run --no-project --python 3.12 python scripts\launcher.py"
    exit /b 0
)

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    start "Heretic Launcher" /min cmd /c ""%USERPROFILE%\.local\bin\uv.exe" run --no-project --python 3.12 python scripts\launcher.py"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 scripts\launcher.py
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "Heretic Launcher" /min cmd /c "py -3 scripts\launcher.py"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "Heretic Launcher" /min cmd /c "python scripts\launcher.py"
    exit /b 0
)

echo Heretic needs the uv package manager, but neither uv nor Python was found.
echo uv is a small, safe package manager from Astral (https://docs.astral.sh/uv/).
echo.
choice /c YN /m "Install uv now"
if errorlevel 2 exit /b 1

powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if not exist "%USERPROFILE%\.local\bin\uv.exe" (
    echo.
    echo uv installation failed. Install it manually from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

start "Heretic Launcher" /min cmd /c ""%USERPROFILE%\.local\bin\uv.exe" run --no-project --python 3.12 python scripts\launcher.py"
exit /b 0
