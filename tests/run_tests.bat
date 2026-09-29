@echo off
rem ============================================================
rem  Subtitle tool - self test runner
rem  Runs:  startup.bat static lint  +  UI smoke test (Node)
rem  ASCII only on purpose: no codepage issues.
rem ============================================================
cd /d "%~dp0.."
title Subtitle Tool - Self Test

set "PYEXE="
set "PYLAUNCH="

if exist "C:\Users\Admin\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=C:\Users\Admin\.workbuddy\binaries\python\versions\3.13.12\python.exe"

if not defined PYEXE (
  for %%P in (py.exe) do if exist "%%~$PATH:P" set "PYLAUNCH=%%~$PATH:P"
)
if not defined PYEXE (
  for %%P in (python.exe) do if exist "%%~$PATH:P" set "PYEXE=%%~$PATH:P"
)
if defined PYEXE (
  if not "%PYEXE:WindowsApps=%"=="%PYEXE%" set "PYEXE="
)

if not defined PYEXE if not defined PYLAUNCH (
  echo.
  echo   [ERROR] Python 3 not found - cannot run the self test.
  echo.
  pause
  exit /b 1
)

chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"

if defined PYEXE (
  "%PYEXE%" "%~dp0run_all.py"
) else (
  "%PYLAUNCH%" -3 "%~dp0run_all.py"
)
set "RC=%ERRORLEVEL%"

echo.
pause
exit /b %RC%
