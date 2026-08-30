@echo off
chcp 65001 >nul
title AI Passport 硬件固件一键烧录工具
echo ========================================================
echo        🚀 FoloToy AI Passport 智能胸卡固件一键烧录工具
echo ========================================================
echo.
echo [1/3] 正在检查 Python 与 esptool 烧录依赖...
python -m pip install esptool pyserial --quiet
if %errorlevel% neq 0 (
    echo [!] 安装 esptool 失败，请检查 Python 与网络环境。
    pause
    exit /b %errorlevel%
)

echo [2/3] 请用 Type-C 数据线将 AI Passport 插入电脑 USB 接口！
echo.
pause

echo.
echo [3/3] 正在执行固件写入 (ESP32-C3 / 8MB Flash / 0x0 合并固件)...
echo 固件文件: FoloToy-AI-Passport-full.bin
echo.

python -m esptool --chip esp32c3 write_flash 0x0 "%~dp0FoloToy-AI-Passport-full.bin"

if %errorlevel% equ 0 (
    echo.
    echo ========================================================
    echo  🎉 恭喜！AI Passport 硬件固件烧录成功！
    echo  卡片已自动重启，开机后即可通过 Wi-Fi TCP 5566 与工作台互联！
    echo ========================================================
) else (
    echo.
    echo [!] 烧录失败，请检查：
    echo     1. 数据线是否具备数据传输功能（非仅充电线）；
    echo     2. 是否按住卡片按键或处于下载模式；
    echo     3. 串口是否被其他串口助手占用。
)
echo.
pause
