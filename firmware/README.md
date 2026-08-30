# 🎴 AI Passport 智能胸卡固件烧录包

本目录包含了 **FoloToy AI Passport 智能胸卡** 烧录所需的全套官方固件与一键烧录工具。

## 📦 固件文件说明

| 文件名 | 烧录地址 (Offset) | 说明 |
| :--- | :--- | :--- |
| **`FoloToy-AI-Passport-full.bin`** | `0x0000` | **推荐：一键合并完整固件 (包含 Bootloader、分区表与应用)** |
| `bootloader.bin` | `0x0000` | ESP32-C3 二级引导程序 |
| `partition-table.bin` | `0x8000` | 8MB Flash 官方分区表 |
| `FoloToy-AI-Passport-app.bin` | `0x10000` | 应用固件主程序 |

---

## 🚀 两种极速烧录方式：

### 方式一：双击批处理一键烧录 (最简单)
1. 用 Type-C 数据线将 AI Passport 胸卡插入电脑 USB 口；
2. 双击运行 **`flash_one_click.bat`**；
3. 工具会自动检测串口并完成全量固件刷入！

### 方式二：网页 WebUSB 刷机
1. 用 Google Chrome 或 Microsoft Edge 浏览器双击打开 **`web_flasher.html`**；
2. 点击 **【⚡ 立即连接卡片并开始烧录】**；
3. 在弹出窗口中选择 USB 串口即可一键自动安装。

---

## 🌐 硬件与《会议智能妙记》工作台通信
* 胸卡开机后连接 Wi-Fi，将自动直连电脑的 TCP `5566` 端口；
* 在工作台完成纪要提炼后，点击【💳 推送到硬件胸卡】，卡片屏幕即可实时呈现会议纪要！
