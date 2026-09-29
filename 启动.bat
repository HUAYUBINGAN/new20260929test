@echo off
cd /d "%~dp0"
title 本地字幕工具

rem ============ 定位 Python，按优先级依次尝试 ============
set "PYEXE="
set "PYLAUNCH="

rem [1] 首选：本机已知的托管 Python。注意路径一律用反斜杠，
rem     cmd.exe 把正斜杠当作命令开关字符，用正斜杠不可靠。
if exist "C:\Users\Admin\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=C:\Users\Admin\.workbuddy\binaries\python\versions\3.13.12\python.exe"

rem [2] 其次：py 启动器，它会自动挑选本机最新的 Python 3
if not defined PYEXE (
  for %%P in (py.exe) do if exist "%%~$PATH:P" set "PYLAUNCH=%%~$PATH:P"
)

rem [3] 再次：PATH 里能直接找到的 python.exe
if not defined PYEXE (
  for %%P in (python.exe) do if exist "%%~$PATH:P" set "PYEXE=%%~$PATH:P"
)

rem [4] 排掉微软应用商店的 python.exe 占位符。那个 exe 只是个跳转器，
rem     运行它只会弹出应用商店，不会真的执行脚本，必须视为无效。
if defined PYEXE (
  if not "%PYEXE:WindowsApps=%"=="%PYEXE%" set "PYEXE="
)

if not defined PYEXE if not defined PYLAUNCH (
  echo.
  echo   [错误] 未找到可用的 Python 3，无法启动。
  echo   请安装 Python 3，或用记事本打开本文件，
  echo   把第 [1] 处那个路径改成你自己的 Python 位置。
  echo.
  pause
  exit /b 1
)

echo.
echo   ============================================
echo      本地字幕工具  -  正在启动
echo   ============================================
echo.
echo   浏览器会自动打开，关闭本窗口即退出程序
echo.

if defined PYEXE (
  "%PYEXE%" "%~dp0server.py"
) else (
  "%PYLAUNCH%" -3 "%~dp0server.py"
)

echo.
echo   程序已退出。
pause
