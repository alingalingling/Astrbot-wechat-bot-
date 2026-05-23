@echo off
chcp 65001 >nul
title 微信 3.9.x 启动 + 版本补丁
setlocal

:: 微信安装路径（根据实际安装位置修改）
:: 常见路径: C:\Program Files\Tencent\WeChat\[3.9.10.19]\WeChat.exe
set WECHAT_PATH=C:\Program Files\Tencent\WeChat\WeChat.exe

echo 正在启动微信...
start "" "%WECHAT_PATH%"

echo 等待微信启动...
timeout /t 5 /nobreak >nul

echo 正在打版本补丁...
python "%~dp0wechat_patcher.py"

echo.
pause
