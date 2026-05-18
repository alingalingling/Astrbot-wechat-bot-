@echo off
chcp 65001 >nul
title 微信机器人 - 启动控制面板

cd /d "%~dp0"
echo 启动 Web 控制面板...
start python bridge.py
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8765
echo 浏览器已打开，请点击"启动"开始运行
