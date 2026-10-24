@echo off
setlocal
cd /d "%~dp0"

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller is not installed. Run: python -m pip install -r requirements-build.txt
    exit /b 1
)

python -m PyInstaller --noconfirm --clean --onefile --windowed --name Prana main.py
if errorlevel 1 exit /b 1

echo Built: dist\Prana.exe
