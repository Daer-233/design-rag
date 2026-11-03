@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Project startup failed with exit code %EXIT_CODE%.
    echo Please run this file as Administrator if an old service process cannot be stopped.
)
echo.
pause
exit /b %EXIT_CODE%
