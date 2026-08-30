# 🚀 飞书会议智能妙记系统 (Feishu Meeting AI Suite)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue?logo=python" alt="Python Version">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Feishu-Open%20Platform-00D6B9?logo=lark" alt="Feishu/Lark">
  <img src="https://img.shields.io/badge/PostgreSQL-16%20Enterprise-336791?logo=postgresql" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

**飞书会议智能妙记系统 (Feishu Meeting AI Suite)** 是一套专为企业打造的全链路智能会议中枢。深度融合 **飞书开放平台 (Feishu/Lark)**、**NewAPI 多大模型网关中枢**、**sillyGirl 9大机器人双向通信框架** 与 **FoloToy AI Passport 智能硬件胸卡**。

---

## 🌟 核心架构与功能特性

### 1. 🎙️ 多源会议输入与高精度语音识别 (ASR)
- **本地长录音批量上传**：支持 MP3 / WAV / M4A 常见格式，真实调用 OpenAI Whisper API 或本地 Faster-Whisper 执行高保真转写。
- **飞书妙记直连解析**：输入飞书妙记链接（`https://feishu.cn/minutes/...`），自动提取 Token 并解析转写内容。
- **速记文本即时提炼**：支持手动粘贴会议草稿、讨论要点进行极速总结。

### 2. 🤖 多 AI 大模型调度中枢 (参考 NewAPI 架构)
- **多模型预设与热切换**：预设 **DeepSeek (V3/R1)**、**火山引擎/豆包 (Doubao)**、**OpenAI (GPT-4o)**、**阿里云通义千问**、**月之暗面 (Kimi)**、**智谱 GLM-4** 及自建 **NewAPI** 统一网关。
- **毫秒级测速与连通性自检**：Web 界面一键测试 API Key 连通性、实时网络延迟与模型版本。
- **5 大垂直业务场景提示词引擎**：通用会议、技术方案评审、产品 PRD 需求、商务交付谈判、头脑风暴研讨。

### 3. 🚀 飞书企业知识生态全自动打通
- 📝 **自动创建飞书云文档 (Docx)**：结构化排版（会议概览、核心决议定案、行动清单 Todo），生成后直接提供飞书官方文档链接，支持一键在浏览器打开。
- 🤖 **飞书群机器人富文本互动卡片**：自动推送到飞书群，支持 HMAC-SHA256 加签鉴权。
- 📊 **多维表格 (Bitable) 待办同步**：将 Todo 事项与负责人、截止日期一键同步至多维表格任务看板。

### 4. 🤖 sillyGirl 风格 9 大机器人双向通信中心
- **双向事件监听 (Webhook Inbound)**：支持飞书（带 challenge 校验）、钉钉、企业微信、Telegram、QQ OneBot、通用 Webhook 回调监听。
- **群聊交互指令**：
  - `@机器人 总结 [讨论记录]`：自动调用 AI 提炼、创建飞书文档并回帖卡片。
  - `@机器人 待办` / `todo`：智能聚合近期未闭环任务。
  - `@机器人 会议` / `历史`：查询最近归档的会议及飞书文档链接。
  - `@机器人 状态` / `ping`：实时汇报 AI 引擎、硬件与数据库状态。
  - `set [bucket] [key] [val]`：动态修改系统 Bucket 键值参数。
- **多路并发广播**：9 大主流渠道并行推送。

### 5. 🎴 FoloToy AI Passport 智能硬件胸卡联动
- **双向 TCP Socket 通信**：监听 5566 端口，采用标准 NDJSON 协议。
- **全自动流水线**：胸卡单击“结束录音”后，自动触发转写、大模型总结、创建飞书文档、广播机器人通知并落库归档。

### 6. 🛡️ 企业级 PostgreSQL 数据库架构 (默认启用)
- **13 大生产表与 10 组索引**：启动时自动执行 DDL 创建与自检 (`organizations`, `users`, `sessions`, `user_configs`, `ai_providers`, `ai_call_logs`, `bot_channels`, `bot_push_logs`, `meeting_records`, `meeting_action_items`, `hardware_devices`, `hardware_device_logs`, `system_buckets`)。
- **多租户数据与密钥隔离**：用户账号加盐鉴权（`salt + sha256`），保障企业数据安全。

---

## 📂 项目目录结构

```text
feishu-meeting-ai-suite/
├── feishu_meeting_tool/          # 核心后端源码包
│   ├── ai_summarizer.py          # 多 AI 大模型调度引擎 (NewAPI)
│   ├── audio_pipeline.py         # 语音转写与 ASR 接入
│   ├── auth_manager.py           # 多租户用户鉴权与会话管理
│   ├── bot_notifier.py           # sillyGirl 9渠道机器人推送中心
│   ├── card_bridge.py            # AI Passport 硬件胸卡 TCP 通信桥接
│   ├── cli.py                    # 命令行终端交互 CLI
│   ├── config.py                 # 全局与多租户配置管理
│   ├── db_engine.py              # PostgreSQL 13 大企业表结构与种子数据
│   ├── feishu_client.py          # 飞书开放平台客户端 (文档/卡片/多维表格)
│   ├── history_manager.py        # 会议纪要历史归档与多租户隔离
│   ├── robot_dispatcher.py       # sillyGirl 双向事件监听与命令路由
│   ├── web_server.py             # FastAPI RESTful API 与 Webhook 网关
│   └── templates/                # 响应式前端 HTML 模板
│       ├── index.html            # 主控制台 (包含全部 6 大业务模块)
│       ├── login.html            # NewAPI 风格现代登录页面
│       └── register.html         # NewAPI 风格独立注册页面
├── tests/                        # 自动化单元测试集
│   ├── test_ai_summarizer.py
│   ├── test_audio_pipeline.py
│   └── test_card_bridge.py
├── .env.example                  # 环境变量模版
├── .gitignore                    # Git 忽略配置
├── Dockerfile                    # Docker 容器构建文件
├── docker-compose.yml            # Docker Compose 一键启动编排
├── requirements.txt              # Python 依赖清单
├── LICENSE                       # MIT 开源许可证
└── README.md                     # 本说明文档
```

---

## ⚡ 快速上手与运行部署

### 方式一：本地 Python 运行

```bash
# 1. 克隆代码仓库
git clone <your-repo-url>
cd feishu-meeting-ai-suite

# 2. 安装依赖
pip install -r requirements.txt

# 3. 复制并配置环境变量 (可选)
cp .env.example .env

# 4. 启动 Web 服务 (默认端口 8000，硬件端口 5566)
python -m feishu_meeting_tool web --host 0.0.0.0 --port 8000
```

访问浏览器工作台：👉 **http://127.0.0.1:8000**

---

### 方式二：Docker Compose 一键全容器化部署 (推荐)

系统内置了完整的一键编排配置，将自动启动 **PostgreSQL 16 企业数据库** 与 **飞书会议智能中枢服务**：

```bash
docker-compose up -d --build
```

---

## 🔑 初始账号说明

- **默认超级管理员账号**：`admin`
- **默认管理员密码**：`admin123`
- *登录后可在系统内注册新账号或修改密码。*

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议开源。
