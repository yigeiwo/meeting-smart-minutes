# 🎯 项目开发准则与 AI 记忆规则 (Global Rules & Memory)

## 🚨 核心强制准则：严禁使用任何模拟数据 (Strictly No Mock / Dummy Data)

1. **绝对真实性原则 (100% Real API & Real Data)**：
   - 本项目在任何阶段、任何模块中，**绝对严禁**使用模拟数据 (Mock Data)、伪造返回值、假模拟按钮（如“模拟扫码”、“模拟推送”等）或虚假数据兜底；
   - 所有第三方平台对接（微信 ClawBot、飞书自建应用、钉钉 Stream、企业微信、QQ OneBot、PushDeer、Telegram、Discord、Bark、PushPlus、Server酱等）必须通过**真实的官方网络协议、真实 API 凭据、真实 WebSocket 长连接与真实 Webhook** 与真实服务端交互。

2. **错误处理真实反映 (Fail Fast & Real Diagnostics)**：
   - 当第三方服务未启动（如 NapCat/GeWeChat 未运行）、网络不可达或 Token 无效时，必须真实抛出并返回详细的真实诊断错误与排查指南，**严禁自动 fallback 到伪造成功的模拟假象**。

3. **数据流转与持久化真实性**：
   - 会议音频、转写文本、AI 提炼纪要、待办事项均来自真实的飞书开放平台、真实的 LLM Gateway (DeepSeek/Qwen/Claude/OpenAI/Ollama) 与真实的 SQLite/PostgreSQL 数据库。

4. **语言要求 (User Global Rule)**：
   - 始终使用中文回复，计划与文档均使用中文。
