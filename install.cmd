@echo off
rem Voice Dictation for Windows: double-click to install or update (no administrator rights needed).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
