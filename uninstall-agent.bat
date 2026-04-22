@echo off
:: uninstall-agent.bat — Run as Administrator

title Control Panel Agent — Uninstaller

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Please right-click and run as Administrator.
    pause
    exit /b 1
)

echo  Stopping agent...
taskkill /f /im ctrl-agent.exe >nul 2>&1

echo  Removing from startup...
del /f /q "%ProgramData%\Microsoft\Windows\Start Menu\Programs\Startup\ctrl-agent.exe" >nul 2>&1

echo  Removing files...
rmdir /s /q "%ProgramData%\CtrlAgent" >nul 2>&1

echo.
echo  Done. Agent has been removed.
pause
