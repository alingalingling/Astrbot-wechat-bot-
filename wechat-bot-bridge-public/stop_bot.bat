@echo off
chcp 65001 >nul
title 微信机器人 - 停止

echo 停止桥接脚本...
taskkill /f /im python.exe /fi "WINDOWTITLE eq bridge*" 2>nul
:: 或者更精确地只杀 bridge.py
wmic process where "commandline like '%%bridge.py%%'" delete 2>nul
echo 已停止

pause
