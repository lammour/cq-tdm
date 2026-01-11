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

:: Check/install pipx
pipx --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo pipx not found. Installing pipx...
    pip install --user pipx
    if errorlevel 1 (
        echo [ERROR] Failed to install pipx
        pause
        exit /b 1
    )
    :: Ensure pipx is in PATH
    python -m pipx ensurepath >nul 2>&1
    echo [OK] pipx installed
    echo.
    echo [NOTE] You may need to restart your terminal for pipx to be in PATH.
    echo        Continuing with python -m pipx...
    set PIPX_CMD=python -m pipx
) else (
    echo [OK] pipx found
    set PIPX_CMD=pipx
)

:: Install cq-tdm with pipx
echo.
echo Installing CQ TDM...
%PIPX_CMD% install cq-tdm --force
if errorlevel 1 (
    echo [ERROR] Failed to install CQ TDM
    pause
    exit /b 1
)
echo [OK] CQ TDM installed

:: pipx installs to %USERPROFILE%\.local\bin on Windows
set SCRIPT_PATH=%USERPROFILE%\.local\bin\cq-tdm.exe

:: Check if exe exists
if not exist "%SCRIPT_PATH%" (
    :: Try pipx default location
    for /f "delims=" %%i in ('python -c "import os; print(os.path.expanduser('~'))"') do set HOMEDIR=%%i
    set SCRIPT_PATH=!HOMEDIR!\.local\bin\cq-tdm.exe
)

if not exist "%SCRIPT_PATH%" (
    echo [WARNING] Could not find cq-tdm.exe automatically.
    echo Try restarting your terminal and running 'cq-tdm'
    set SCRIPT_PATH=%USERPROFILE%\.local\bin\cq-tdm.exe
)

echo [OK] Executable: %SCRIPT_PATH%

:: Download icon from GitHub
echo.
echo Downloading icon...
set ICON_DIR=%LOCALAPPDATA%\CQ TDM
set ICON_PATH=%ICON_DIR%\icon.ico
if not exist "%ICON_DIR%" mkdir "%ICON_DIR%"

powershell -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/lammour/cq-tdm/main/assets/icon.ico' -OutFile '%ICON_PATH%'" >nul 2>&1
if exist "%ICON_PATH%" (
    echo [OK] Icon downloaded
) else (
    echo [NOTE] Could not download icon, using default
    set ICON_PATH=%SCRIPT_PATH%
)

:: Create Start Menu shortcut
echo.
echo Creating Start Menu shortcut...
set STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs
set SHORTCUT=%STARTMENU%\CQ TDM.lnk

:: Create shortcut using PowerShell
powershell -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $s = $ws.CreateShortcut('%SHORTCUT%'); ^
     $s.TargetPath = '%SCRIPT_PATH%'; ^
     $s.WorkingDirectory = '%USERPROFILE%'; ^
     $s.IconLocation = '%ICON_PATH%'; ^
     $s.Description = 'CT Scanner Internal Quality Control Software'; ^
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
         $s.Description = 'CT Scanner Internal Quality Control Software'; ^
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
echo To uninstall later: pipx uninstall cq-tdm
echo.
pause
