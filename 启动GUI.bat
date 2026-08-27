@echo off
setlocal
title TNO UI 换色 Mod 生成器
cd /d "%~dp0"
set "PY="
where python >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PY=py -3"
    )
)
if not defined PY (
    echo [错误] 未找到 Python。请安装 Python 3.8+（安装时勾选 "Add python.exe to PATH"）。
    echo        下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo ----------------------------------------------------------------
echo  TNO UI 换色 Mod 生成器
echo  正在启动本地服务，浏览器将自动打开...
echo  关闭本窗口即退出。如需重新打开界面:
echo  http://127.0.0.1:8765
echo ----------------------------------------------------------------
%PY% tno_web_gui.py
pause