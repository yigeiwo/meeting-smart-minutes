@echo off
chcp 65001 >nul
title AI Passport 硬件固件一键烧录工具
echo ========================================================
echo        🚀 FoloToy AI Passport 智能胸卡固件一键烧录工具
echo ========================================================
echo.
python "%~dp0flash_tool.py"
if %errorlevel% neq 0 (
    echo.
    echo [!] 尝试使用保底模式自动烧录 (115200)...
    python -m esptool --chip esp32c3 -b 115200 write-flash --flash-mode dio --flash-freq 40m --flash-size 8MB 0x0 "%~dp0FoloToy-AI-Passport-full.bin"
)
echo.
pause
