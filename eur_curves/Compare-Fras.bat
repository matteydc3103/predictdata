@echo off
rem Compare two FRA snaps. Keep this in the same folder as Refresh-EURCurves.ps1.
rem   Compare-Fras.bat                         asks for the dates
rem   Compare-Fras.bat 10-11-2026 12-11-2026   compares two closing snaps
rem   Compare-Fras.bat 10-11-2026              compares that snap with live rates
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Refresh-EURCurves.ps1" -Compare -Date1 "%~1" -Date2 "%~2"
pause
