@echo off
:: install-agent.bat
:: Run as Administrator on any machine to install the control panel agent.
:: Just place this .bat file next to ctrl-agent.exe and double-click.

title Control Panel Agent — Installer

:: ── CHECK ADMIN ──────────────────────────────────────────────
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Please right-click and run as Administrator.
    echo.
    pause
    exit /b 1
)

:: ── CHECK EXE EXISTS ─────────────────────────────────────────
if not exist "%~dp0ctrl-agent.exe" (
    echo.
    echo  [ERROR] ctrl-agent.exe not found next to this batch file.
    echo          Make sure both files are in the same folder.
    echo.
    pause
    exit /b 1
)

echo.
echo  =============================================
echo   CONTROL PANEL AGENT — INSTALLER
echo  =============================================
echo.

:: ── INSTALL TO PROGRAMDATA ───────────────────────────────────
set INSTALL_DIR=%ProgramData%\CtrlAgent
echo  [1/3] Installing to %INSTALL_DIR%...
mkdir "%INSTALL_DIR%" >nul 2>&1
copy /y "%~dp0ctrl-agent.exe" "%INSTALL_DIR%\ctrl-agent.exe" >nul
if %errorlevel% neq 0 (
    echo  [ERROR] Failed to copy exe. Is it already running?
    pause
    exit /b 1
)
echo         OK

:: ── ADD TO ALL-USERS STARTUP ─────────────────────────────────
echo  [2/3] Adding to startup (all users)...
set STARTUP=%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup
copy /y "%INSTALL_DIR%\ctrl-agent.exe" "%STARTUP%\ctrl-agent.exe" >nul
echo         OK

:: ── KILL OLD INSTANCE AND START FRESH ────────────────────────
echo  [3/3] Starting agent...
taskkill /f /im ctrl-agent.exe >nul 2>&1
start "" /B "%INSTALL_DIR%\ctrl-agent.exe"
echo         OK

echo.
echo  =============================================
echo   Done! Agent is running and will auto-start
echo   on boot for all users on this machine.
echo.
echo   To uninstall, run uninstall-agent.bat
echo  =============================================
echo.
pause
