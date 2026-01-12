@echo off
setlocal EnableDelayedExpansion

echo.
echo ========================================
echo   CQ TDM Installer for Windows
echo ========================================
echo.

:: Check if Python is installed
set "PYTHON="

:: First check if python is in PATH
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    goto :python_found
)

:: Check common install locations (newest first)
for %%V in (314 313 312 311 310) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :python_found
    )
    if exist "C:\Program Files\Python%%V\python.exe" (
        set "PYTHON=C:\Program Files\Python%%V\python.exe"
        goto :python_found
    )
)

:: Python not found, attempt to install
echo Python not found. Attempting to install Python 3.14...
echo.

:: Try winget first (Windows 10/11)
winget --version >nul 2>&1
if not errorlevel 1 (
    echo Installing Python via winget...
    winget install Python.Python.3.14 --silent --accept-package-agreements --accept-source-agreements
    if not errorlevel 1 (
        set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        goto :python_found
    )
    echo [WARNING] winget install failed, trying direct download...
)

:: Fallback: download installer directly
echo Downloading Python installer...
set "PYTHON_INSTALLER=%TEMP%\python-installer.exe"
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe' -OutFile '%PYTHON_INSTALLER%'"

if not exist "%PYTHON_INSTALLER%" (
    echo [ERROR] Failed to download Python installer.
    echo Please install Python 3.10+ manually from https://python.org
    pause
    exit /b 1
)

echo Installing Python (this may take a minute)...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1
if errorlevel 1 (
    echo [ERROR] Python installation failed.
    echo Please install Python 3.10+ manually from https://python.org
    del "%PYTHON_INSTALLER%" >nul 2>&1
    pause
    exit /b 1
)
del "%PYTHON_INSTALLER%" >nul 2>&1

:: Set path to newly installed Python
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"

:: Verify installation
if not exist "%PYTHON%" (
    echo [ERROR] Python installation completed but executable not found.
    echo Please install Python 3.10+ manually from https://python.org
    pause
    exit /b 1
)

echo [OK] Python installed successfully

:python_found
:: Get Python version
for /f "tokens=2" %%i in ('"%PYTHON%" --version 2^>^&1') do set PYVER=%%i
echo [OK] Python %PYVER% found

:: Check/install pipx
set "USE_PIPX_MODULE=0"
pipx --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo pipx not found. Installing pipx...
    "%PYTHON%" -m pip install --user pipx
    if errorlevel 1 (
        echo [ERROR] Failed to install pipx
        pause
        exit /b 1
    )
    :: Ensure pipx is in PATH
    "%PYTHON%" -m pipx ensurepath >nul 2>&1
    echo [OK] pipx installed
    set "USE_PIPX_MODULE=1"
) else (
    echo [OK] pipx found
)

:: Install cq-tdm with pipx
echo.
echo Installing CQ TDM...
if "%USE_PIPX_MODULE%"=="1" (
    "%PYTHON%" -m pipx install cq-tdm --force
) else (
    pipx install cq-tdm --force
)
if errorlevel 1 (
    echo [ERROR] Failed to install CQ TDM
    pause
    exit /b 1
)
echo [OK] CQ TDM installed

:: pipx installs to %USERPROFILE%\.local\bin on Windows
:: Use cq-tdm-gui.exe (GUI script) instead of cq-tdm.exe to avoid console window
set SCRIPT_PATH=%USERPROFILE%\.local\bin\cq-tdm-gui.exe

:: Check if exe exists
if not exist "%SCRIPT_PATH%" (
    :: Try pipx default location
    for /f "delims=" %%i in ('"%PYTHON%" -c "import os; print(os.path.expanduser('~'))"') do set HOMEDIR=%%i
    set SCRIPT_PATH=!HOMEDIR!\.local\bin\cq-tdm-gui.exe
)

if not exist "%SCRIPT_PATH%" (
    echo [WARNING] Could not find cq-tdm-gui.exe automatically.
    echo Try restarting your terminal and running 'cq-tdm-gui'
    set SCRIPT_PATH=%USERPROFILE%\.local\bin\cq-tdm-gui.exe
)

echo [OK] Executable: %SCRIPT_PATH%

:: Download icon from GitHub
echo.
echo Downloading icon...
set ICON_DIR=%LOCALAPPDATA%\CQ TDM
set ICON_PATH=%ICON_DIR%\icon.ico
if not exist "%ICON_DIR%" mkdir "%ICON_DIR%"

powershell -Command "Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/lammour/cq-tdm/main/src/cq_tdm/assets/icon.ico' -OutFile '%ICON_PATH%'" >nul 2>&1
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
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%SCRIPT_PATH%'; $s.WorkingDirectory = '%USERPROFILE%'; $s.IconLocation = '%ICON_PATH%'; $s.Description = 'CT Scanner Internal Quality Control Software'; $s.Save()"

if exist "%SHORTCUT%" (
    echo [OK] Start Menu shortcut created
) else (
    echo [WARNING] Could not create shortcut
)

:: Handle Desktop shortcut
set DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\CQ TDM.lnk
if exist "%DESKTOP_SHORTCUT%" (
    :: Update existing shortcut (handles upgrades from older versions)
    echo Updating existing Desktop shortcut...
    powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP_SHORTCUT%'); $s.TargetPath = '%SCRIPT_PATH%'; $s.WorkingDirectory = '%USERPROFILE%'; $s.IconLocation = '%ICON_PATH%'; $s.Description = 'CT Scanner Internal Quality Control Software'; $s.Save()"
    echo [OK] Desktop shortcut updated
) else (
    :: Offer to create new shortcut
    echo.
    set /p DESKTOP_CHOICE="Create Desktop shortcut? (Y/N): "
    if /i "!DESKTOP_CHOICE!"=="Y" (
        powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP_SHORTCUT%'); $s.TargetPath = '%SCRIPT_PATH%'; $s.WorkingDirectory = '%USERPROFILE%'; $s.IconLocation = '%ICON_PATH%'; $s.Description = 'CT Scanner Internal Quality Control Software'; $s.Save()"
        echo [OK] Desktop shortcut created
    )
)

echo.
echo ========================================
echo   Installation complete!
echo ========================================
echo.
echo You can now:
echo   - Find "CQ TDM" in your Start Menu
echo   - Run "cq-tdm-gui" from the command line (or "cq-tdm" for console mode)
echo.
echo To uninstall later: pipx uninstall cq-tdm
echo.
pause
