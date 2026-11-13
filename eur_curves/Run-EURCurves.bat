@echo off
rem Double-click this each morning. Keep it in the same folder as Refresh-EURCurves.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Refresh-EURCurves.ps1" %*
pause
