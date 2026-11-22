@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    python -m venv .venv
)

echo [2/4] Installing desktop runtime & PyInstaller...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo [3/4] Generating WooodSubs app icon...
".venv\Scripts\python.exe" assets\generate_icon.py

echo [4/4] Building standalone Windows EXE...
".venv\Scripts\pyinstaller.exe" --clean --noconfirm wx20.spec

echo.
echo Done! Distributable file:
echo   dist\WooodSubs.exe
pause
