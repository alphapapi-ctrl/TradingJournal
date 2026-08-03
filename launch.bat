@echo off
rem Trading Journal launcher — double-click to start (or focus) the app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1"
if errorlevel 1 pause
