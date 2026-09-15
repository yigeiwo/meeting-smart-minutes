# 🎴 AI Passport 智能胸卡固件烧录包

本目录包含了 **FoloToy AI Passport 智能胸卡** 烧录所需的全套官方固件与一键烧录工具。

> ⚠️ **重要：请使用最新固件**
> 本目录下的 `.bin` 是**预编译快照**。若你刚更新过 `ai-passport/` 源码，必须**重新编译并替换**
> 这些 bin 再烧录，否则胸卡仍运行旧固件。当前快照对应 tag **`v1.2.6`**
> （应用 `v1.2.6`，2,570,640 字节合并镜像，应用段 2,505,104 字节约 2.39MB）。
> 推荐走仓库的 GitHub Actions 云端编译：推送到 `main` 会自动构建，打 `v*` 标签会自动
> 发布带固件的 Release，可直接下载 `FoloToy-AI-Passport-full.bin`。
> 本地编译方式：
> ```bash
> cd ai-passport
> idf.py set-target esp32c3
> idf.py build
> # 生成合并镜像
> idf.py merge-bin -o ../firmware/FoloToy-AI-Passport-full.bin
> ```

> 💡 **升级刷机避坑（保留 Wi-Fi 配网）**
> 整包 `full.bin` 直接刷 `0x0` 会连带擦掉 `0x9000` 起的 **NVS 分区**，胸卡将丢失
> Wi-Fi 凭证与设备令牌、必须重新 BLE 配网。**日常升级只刷应用分区**：
> ```bash
> esptool --chip esp32c3 --port COM3 write_flash 0x10000 FoloToy-AI-Passport-app.bin
> ```
> 首次全新烧录（或愿意重新配网）才使用整包 `full.bin` 刷 `0x0`。

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

**真实机制（以固件源码为准）：**

* **配网**：胸卡开机后由 `ble_prov_start()` 启动 BLE 蓝牙配网服务，广播名 `FoloPassport`
  （GATT Service `0xFFF0` / 写特征 `0xFFF1` / 通知+读取特征 `0xFFF2`）。手机或电脑用
  Chrome / Edge 打开工作台配网页 `/wifi`，通过 Web Bluetooth 连接并下发
  `{"ssid":"..","pwd":"..","host":"ai.shuoyunqi.online","port":443,"tls":true,"path":"/ws/card","token":".."}`，
  全部参数写入胸卡 NVS，**下次开机自动回连**。
  工作台地址支持**局域网 IP 或公网域名**（由 `esp_websocket_client` 内部完成 DNS 解析）。
  配网报文较长，已把 `CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU` 提到 512 以保证单次写入下发完整。
* **桥接通道（已打通，走 443 复用站点证书）**：`main/card_link.c` 是固件侧真实 WebSocket 客户端，
  默认连接 `wss://<工作台域名>:443/ws/card?token=<设备令牌>`。
  由 nginx 在 443 上终止 TLS，因此**无需在云安全组额外放行 5566**。
  内置自动重连（3 秒退避）与 15 秒心跳；TLS 使用 ESP-IDF 证书包做**真实证书校验**
  （本域名链路根为 `DigiCert Global Root G2`），不做任何跳过校验的处理。
  同时也兼容局域网直连 `ws://<IP>:5566/ws/card`（明文，仅建议内网调试）。
* **设备令牌**：工作台侧 `CARD_WS_TOKEN` 用于拒绝非本系统胸卡的连接，令牌错误会被立即断开。
* **上行（卡片 → 工作台）**：`hello` 握手、`battery` 电量、`record_start` / `record_stop`
  录音启停、`ping` 心跳、`combo_menu` 返回菜单、`select_mode` 进入模式上报
  （工作台收到 `select_mode: meeting` 会立即重发最近纪要，实现切回会议模式恢复显示），
  以及 **`audio` 真实录音音频流**（16kHz / 16bit / 单声道 PCM，base64 封装，每帧 1024 字节）。
* **下行（工作台 → 卡片）**：`pong` 心跳应答、`meeting_processed` / `summary`
  真实纪要（标题 / 飞书云文档链接 / 待办 / 决议）、`pipeline_result` 提炼失败原因、
  `cmd` 反向控制指令。
* **完整闭环**：胸卡按 OK 开始录音 → 音频实时推给工作台并落盘为 `records/card_*.wav`
  → 停止录音后工作台跑真实 AI 流水线（转写 → 大模型提炼 → 创建飞书云文档 → 多机器人广播）
  → 结果回推胸卡 → 屏幕第 1 页显示真实纪要标题与云文档状态，第 3 页显示真实待办清单与核心决议
  （`[决]` 前缀；没有待办时决议独占整页）。
* **提炼异步提醒**：AI 提炼耗时较长时无需等待——提炼完成后无论卡片处于哪个模式，
  屏幕顶部都会弹出提醒横幅；切回会议模式时通过 `select_mode` 触发纪要重发，纪要不会丢失。
* **真实错误透传**：链路断开、工作台提炼失败、超过 180 秒未返回，胸卡屏幕都会如实显示
  对应原因，**不会伪造"已同步成功"**。
* **中文字库**：固件内置 `font_chinese_14`（14px / 4bpp / 黑体）已扩容为 **GB2312 全集**
  （6763 个汉字 + 682 个符号 + ASCII，共 7541 个字形）。实测字库数据占用 **511,036 字节 (约 0.49MB)**，
  整机应用段 2.38MB，仍在 3MB 分区上限内。
  会议纪要中的常规中文（含"条、品、钟、但、嵌、员、传、噪"等此前会留空的字）均可正常显示。
  仅极少数 GB2312 之外的生僻字仍会留空，如需支持请再扩展字库。
* **屏幕显示限制**：单行标签限宽 212px、正文限宽 196px；工作台回推的超长标题/待办会由
  固件按真实字宽自动截断并加 ".."，不会溢出或裁切。
