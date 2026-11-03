@echo off
setlocal
cd /d "%~dp0"
title Work Knowledge Assistant - Start
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\manage-knowledge.ps1" -Action Start
set "KNOWLEDGE_EXIT_CODE=%ERRORLEVEL%"
echo.
if /I not "%~1"=="--no-pause" pause
exit /b %KNOWLEDGE_EXIT_CODE%
