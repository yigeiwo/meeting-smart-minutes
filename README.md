# 飞书会议智能妙记系统 (Feishu Meeting Minutes AI Suite)

深度融合 **飞书开放平台 (Feishu/Lark)**、**NewAPI 多大模型中枢 (Multi-LLM)**、**sillyGirl 9大机器人双向通信框架** 与 **FoloToy AI Passport 智能胸卡** 的全链路企业级会议纪要生成系统。

---

## 🌟 核心特性矩阵

- 🎙️ **多模态会议输入**：支持本地长录音（MP3/WAV/M4A）、会议速记文本导入、飞书妙记直连解析与 AI Passport 硬件胸卡实时录音流。
- 🤖 **多 AI 大模型调度中枢 (参考 NewAPI 架构)**：
  - 深度支持 **DeepSeek (V3/R1)**、**火山引擎/豆包 (Doubao)**、**OpenAI (GPT-4o)**、**阿里云百炼 (通义千问)**、**月之暗面 (Kimi)**、**智谱 GLM-4** 及自建 NewAPI 网关。
  - 100% 真实执行，支持密钥与 Base URL 热切换、实时延迟测速。
- 📑 **5 大场景结构化总结**：
  - 通用会议、技术方案评审、产品 PRD 需求、商务交付谈判、头脑风暴研讨。
  - 精准提炼 **💡 会议概览**、**🎯 核心决议定案** 与 **✅ 行动项清单 (Todo & 负责人 & 截止时间)**。
- 🚀 **飞书全生态深度打通**：
  - 📝 **自动创建飞书云文档 (Docx)**：富文本排版，一键生成官方云文档并返回直达链接。
  - 🤖 **飞书群机器人互动卡片**：自动推送决策卡片，支持带签名加签鉴权。
  - 📊 **多维表格 (Bitable) 待办自动同步**：将 Todo 事项批量写入多维表格。
- 🤖 **sillyGirl 风格 9 大机器人双向通信中心**：
  - **双向事件监听**：支持飞书、钉钉、企微、Telegram、OneBot QQ、通用 Webhook 接收。
  - **群内交互指令**：`@机器人 总结 [文本]`、`@机器人 待办`、`@机器人 会议`、`@机器人 状态`、`set [bucket] [key] [val]`。
  - **多路并发广播**：9 大主流渠道并行推送。
- 🎴 **AI Passport 硬件联动**：
  - 支持 ESP32-C3 实体胸卡（LVGL 三页看板：会议录音与纪要 / 蓝牙配网与工作台链路 / 多维表格待办）。
  - 胸卡通过真实 BLE Web Bluetooth 配网（广播名 `FoloPassport`），并用内置 TCP/NDJSON 客户端
    直连工作台 `5566` 端口：**上行真实录音音频流**，下行接收 AI 纪要回推。
  - 完整闭环：卡片录音 → 工作台落盘 `records/card_*.wav` → 真实 AI 提炼 + 飞书云文档 →
    结果回推卡片屏幕真实展示（失败原因同样如实回传，不伪造成功）。
- 🛡️ **企业级 PostgreSQL 数据库架构**：
  - 用户账号与加盐安全认证（`salt + sha256`），用户多租户数据与 AI 密钥隔离。
  - 会议纪要与飞书文档历史归档持久化。

---

## 🚀 快速启动

```bash
# 启动 Web 工作台 (默认端口 8000，硬件通信端口 5566)
python -m feishu_meeting_tool web --host 127.0.0.1 --port 8000

# 命令行 CLI 直接总结
python -m feishu_meeting_tool summarize sample_meeting.txt --push-bot
```

访问工作台：👉 [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## 🌐 线上部署（已上线）

| 项目 | 值 |
| :--- | :--- |
| 线上地址 | **https://ai.shuoyunqi.online** |
| 服务器 | `43.155.248.215`（Debian 13 / Docker） |
| 部署目录 | `/root/ai-passport-suite` |
| Web 容器 | `feishu-meeting-app`（Uvicorn 8000，仅容器内网，由 nginx 反代） |
| 桥接端口 | `5566`（NDJSON，硬件胸卡直连） |
| 数据库 | `feishu-meeting-postgres`（PostgreSQL 16，仅内网） |
| 反向代理 | 复用现有 `new-api-nginx` 容器，站点配置 `ai.shuoyunqi.online.conf` |
| SSL 证书 | `/root/new-api/certs/ai.shuoyunqi.online_bundle.crt` + `.key` |

**页面入口**

- 工作台首页：`https://ai.shuoyunqi.online/`
- 蓝牙配网页：`https://ai.shuoyunqi.online/wifi`
- NFC 碰一碰页：`https://ai.shuoyunqi.online/nfc`
- 固件下载：`https://ai.shuoyunqi.online/api/firmware/download`
- 胸卡桥接通道：`wss://ai.shuoyunqi.online/ws/card?token=<CARD_WS_TOKEN>`

**账号与注册**

- 管理员账号：`admin`（登录后可自行在「多 AI 模型与系统配置」里改密码）
- **已关闭自助注册**：`/register` 页面与 `/api/auth/register` 接口均返回 403，
  登录页与首页也不再显示注册入口。需要建号时由管理员操作。

**胸卡如何连上服务器（无需开 5566）**

胸卡的桥接通道走 `wss://`（WebSocket over TLS），由 nginx 在 **443** 端口终止 TLS，
与网站共用同一张证书，因此云安全组只需保持 80/443 放行即可。
`5566` 仅保留给"局域网直连本地工作台"的调试场景，公网不需要放行。

为防他人伪造胸卡报文，`/ws/card` 启用了设备令牌校验（`.env` 里的 `CARD_WS_TOKEN`）。
在配网页 `/wifi` 的「设备令牌」栏填入同一个值即可。

**更新代码后重新部署**

```bash
ssh root@43.155.248.215
cd /root/ai-passport-suite
docker compose up -d --build
```

**还需要你在 `.env` 里补真实凭据**

编辑 `/root/ai-passport-suite/.env`，填入飞书自建应用与大模型 Key（`FEISHU_*`、`LLM_API_KEY` 等），
然后 `docker compose up -d` 重启生效。未填写时相关功能会**如实失败并把错误原因回传到胸卡屏幕**，
不会伪造成功。

> 注：`firmware/` 下的 `.bin` 是预编译快照，修改 `ai-passport/` 固件源码后需要重新编译
> 并替换，详见 `firmware/README.md`。
