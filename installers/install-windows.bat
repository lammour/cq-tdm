@echo off
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   CQ TDM Installer for Windows
echo ========================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Get Python version
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER% found

:: Install/upgrade cq-tdm
echo.
echo Installing CQ TDM...
pip install --upgrade cq-tdm
if errorlevel 1 (
    echo [ERROR] Failed to install CQ TDM
    pause
    exit /b 1
)
echo [OK] CQ TDM installed

:: Find the executable path
for /f "delims=" %%i in ('python -c "import sys; print(sys.prefix)"') do set PYTHON_PREFIX=%%i
set SCRIPT_PATH=%PYTHON_PREFIX%\Scripts\cq-tdm.exe

:: Check if exe exists, if not try user scripts
if not exist "%SCRIPT_PATH%" (
    for /f "delims=" %%i in ('python -c "import site; print(site.getusersitepackages().replace('site-packages','Scripts'))"') do set SCRIPT_PATH=%%i\cq-tdm.exe
)

if not exist "%SCRIPT_PATH%" (
    echo [WARNING] Could not find cq-tdm.exe automatically.
    echo You can still run the app with: python -m cq_tdm
    pause
    exit /b 0
)

echo [OK] Found executable: %SCRIPT_PATH%

:: Create Start Menu shortcut
echo.
echo Creating Start Menu shortcut...
set STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs
set SHORTCUT=%STARTMENU%\CQ TDM.lnk

:: Icon path (will use exe icon if ico file doesn't exist)
set ICON_PATH=%~dp0..\assets\icon.ico
if not exist "%ICON_PATH%" set ICON_PATH=%SCRIPT_PATH%

:: Create shortcut using PowerShell
powershell -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $s = $ws.CreateShortcut('%SHORTCUT%'); ^
     $s.TargetPath = '%SCRIPT_PATH%'; ^
     $s.WorkingDirectory = '%USERPROFILE%'; ^
     $s.IconLocation = '%ICON_PATH%'; ^
     $s.Description = 'CT Scanner Quality Control Software'; ^
     $s.Save()"

if exist "%SHORTCUT%" (
    echo [OK] Start Menu shortcut created
) else (
    echo [WARNING] Could not create shortcut
)

:: Offer to create Desktop shortcut
echo.
set /p DESKTOP_CHOICE="Create Desktop shortcut? (Y/N): "
if /i "%DESKTOP_CHOICE%"=="Y" (
    set DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\CQ TDM.lnk
    powershell -Command ^
        "$ws = New-Object -ComObject WScript.Shell; ^
         $s = $ws.CreateShortcut('!DESKTOP_SHORTCUT!'); ^
         $s.TargetPath = '%SCRIPT_PATH%'; ^
         $s.WorkingDirectory = '%USERPROFILE%'; ^
         $s.IconLocation = '%ICON_PATH%'; ^
         $s.Description = 'CT Scanner Quality Control Software'; ^
         $s.Save()"
    echo [OK] Desktop shortcut created
)

echo.
echo ========================================
echo   Installation complete!
echo ========================================
echo.
echo You can now:
echo   - Find "CQ TDM" in your Start Menu
echo   - Run "cq-tdm" from the command line
echo.
pause
