# -*- coding: utf-8 -*-
import re
import time
import json
import logging
from typing import Dict, Any, List, Optional, Callable
from pathlib import Path

logger = logging.getLogger("robot_dispatcher")
BUCKETS_FILE = Path("records/buckets.json")


class BucketStore:
    """参考 sillyGirl 的 Bucket 动态配置系统"""

    @classmethod
    def _ensure(cls):
        BUCKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not BUCKETS_FILE.exists():
            with open(BUCKETS_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

    @classmethod
    def get(cls, bucket: str, key: str, default: Any = "") -> Any:
        cls._ensure()
        try:
            with open(BUCKETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(bucket, {}).get(key, default)
        except Exception:
            return default

    @classmethod
    def set(cls, bucket: str, key: str, value: Any) -> bool:
        cls._ensure()
        try:
            with open(BUCKETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        if bucket not in data:
            data[bucket] = {}
        data[bucket][key] = value

        with open(BUCKETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[Bucket] 设置成功: {bucket}.{key} = {value}")
        return True

    @classmethod
    def get_all(cls) -> Dict[str, Any]:
        cls._ensure()
        try:
            with open(BUCKETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


class RobotDispatcher:
    """参考 sillyGirl 架构的双向命令调度与规则匹配引擎"""

    @classmethod
    def handle_incoming_message(
        cls,
        platform: str,
        sender_id: str,
        sender_name: str,
        message_text: str,
        chat_id: Optional[str] = None,
        raw_event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = (message_text or "").strip()

        # 1. 自动反序列化 JSON 文本包 (如飞书开放平台的 content 字段)
        if text.startswith("{") and text.endswith("}"):
            try:
                js_t = json.loads(text)
                if isinstance(js_t, dict) and "text" in js_t:
                    text = str(js_t["text"]).strip()
            except Exception:
                pass

        # 2. 自动剔除群聊中的 @机器人 占位符与斜杠指令前缀 (如 @_user_1, @机器人, /bot 等)
        text = re.sub(r"^@\S+\s*", "", text).strip()
        text = re.sub(r"^/\S+\s*", "", text).strip()

        logger.info(f"[{platform}] 收到来自 {sender_name}({sender_id}) 的消息: {text[:60]}")

        # 1. 帮助菜单
        if re.match(r"^(help|帮助|菜单|\?|？|/start)$", text, re.I):
            return {
                "type": "text",
                "content": (
                    "🤖 飞书会议智能总结机器人指令菜单 (sillyGirl 驱动)：\n\n"
                    "1. 【智能总结】直接发送会议讨论内容，或输入：\n"
                    "   `总结 今天讨论了飞书文档上线排期与人员分工...`\n\n"
                    "2. 【查询待办】输入 `待办` 或 `todo` 查看当前最新行动项\n\n"
                    "3. 【历史纪要】输入 `会议` 或 `历史` 查看最近归档的会议及飞书文档链接\n\n"
                    "4. 【系统状态】输入 `状态` 或 `status` 查看当前 AI 引擎与硬件连接状态\n\n"
                    "5. 【配置修改】输入 `set [模块] [配置项] [值]` 动态设置系统参数\n"
                    "   例如：`set ai active_model deepseek-chat`"
                ),
            }

        # 2. 系统状态
        if re.match(r"^(status|状态|ping)$", text, re.I):
            from .config import get_config
            cfg = get_config()
            enabled_bots = [ch.get("name") for ch in cfg.bot_channels if ch.get("enabled")]
            return {
                "type": "text",
                "content": (
                    f"⚡ 系统运行状态报告：\n"
                    f"• 当前激活 AI 引擎: {cfg.active_llm_id.upper()} ({cfg.llm_model})\n"
                    f"• 飞书自建应用: {'已配置' if cfg.feishu_app_id else '未配置'}\n"
                    f"• 数据库模式: {'PostgreSQL 企业数据库' if cfg.pg_enabled else '本地高可用模式'}\n"
                    f"• 已启用推送渠道: {', '.join(enabled_bots) if enabled_bots else '暂无'}\n"
                    f"• 硬件卡片通信端口: {cfg.bridge_port} (TCP Socket 就绪)"
                ),
            }

        # 3. 查看待办列表
        if re.match(r"^(待办|todo|tasks|行动项)$", text, re.I):
            from .history_manager import get_meeting_history
            records = get_meeting_history()
            if not records:
                return {"type": "text", "content": "暂无待办事项记录，您可以开始一场新会议并生成总结！"}

            latest_todos = []
            for r in records[:3]:
                for t in r.get("todos", []):
                    latest_todos.append(f"• [{t.get('priority', 'P1')}] {t.get('task')} (👤 负责人: {t.get('owner', '待定')} | ⏳ 截止: {t.get('due', '近期')})")

            if not latest_todos:
                return {"type": "text", "content": "🎉 近期所有会议均无未完成待办事项！"}

            return {
                "type": "text",
                "content": f"📋 近期会议核心行动项待办清单 ({len(latest_todos)} 项)：\n\n" + "\n".join(latest_todos[:10]),
            }

        # 4. 查看最近会议与文档链接
        if re.match(r"^(会议|历史|纪要|history)$", text, re.I):
            from .history_manager import get_meeting_history
            records = get_meeting_history()
            if not records:
                return {"type": "text", "content": "暂无归档会议纪要。"}

            msg = "📑 最近归档的会议纪要列表：\n\n"
            for idx, r in enumerate(records[:5], 1):
                doc_part = f"\n   👉 飞书云文档: {r.get('doc_url')}" if r.get('doc_url') else ""
                msg += f"{idx}. 【{r.get('title')}】({r.get('date')}){doc_part}\n"
            return {"type": "text", "content": msg}

        # 5. set [bucket] [key] [val] 动态配置
        set_match = re.match(r"^set\s+([a-zA-Z0-9_\-]+)\s+([a-zA-Z0-9_\-]+)\s+(.+)$", text, re.I)
        if set_match:
            b_name, b_key, b_val = set_match.groups()
            BucketStore.set(b_name, b_key, b_val)
            return {
                "type": "text",
                "content": f"✔ 配置已更新：{b_name}.{b_key} = {b_val}",
            }

        # 6. 智能总结指令：以“总结”开头或文本长度大于 30 字
        summary_target_text = ""
        if re.match(r"^(总结|纪要|提炼|会议纪要)\s*(.*)$", text):
            summary_target_text = re.sub(r"^(总结|纪要|提炼|会议纪要)\s*", "", text).strip()
        elif len(text) >= 40:
            summary_target_text = text

        if summary_target_text:
            return cls._perform_ai_summary_and_reply(summary_target_text, sender_name)

        # 兜底默认回复
        return {
            "type": "text",
            "content": f"收到指令：'{text}'。输入【帮助】可查看全部支持的指令，或直接发送会议讨论内容让我为您自动总结！",
        }

    @classmethod
    def _perform_ai_summary_and_reply(cls, transcript_text: str, sender_name: str) -> Dict[str, Any]:
        from .config import get_config
        from .ai_summarizer import AISummarizer
        from .feishu_client import FeishuClient
        from .history_manager import save_meeting_record
        from .bot_notifier import BotNotifier

        cfg = get_config()
        if not cfg.llm_api_key:
            return {
                "type": "text",
                "content": "❌ 系统未配置大模型 API Key，请先登录 Web 工作台配置 AI 模型密钥后再使用智能总结！",
            }

        try:
            summarizer = AISummarizer(
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url,
                model=cfg.llm_model,
                temperature=cfg.llm_temperature,
            )
            summary_data = summarizer.summarize(transcript_text, scenario=cfg.default_scenario)

            # 自动创建飞书云文档
            doc_url = ""
            if cfg.feishu_app_id and cfg.feishu_app_secret:
                try:
                    feishu = FeishuClient(
                        app_id=cfg.feishu_app_id,
                        app_secret=cfg.feishu_app_secret,
                        doc_folder_token=cfg.feishu_doc_folder_token,
                    )
                    doc_url = feishu.create_feishu_doc(summary_data)
                    summary_data["doc_url"] = doc_url
                except Exception as e:
                    logger.warning(f"自动创建飞书文档失败: {e}")

            # 自动归档
            save_meeting_record(summary_data, doc_url=doc_url, scenario=cfg.default_scenario, source="bot_chat", username=sender_name)

            # 格式化回复内容
            md_reply = BotNotifier.format_summary_markdown(summary_data, doc_url=doc_url)
            return {
                "type": "markdown",
                "title": summary_data.get("title", "会议智能纪要"),
                "content": f"🎉 **会议智能总结已生成 (触发者: {sender_name})**：\n\n" + md_reply,
                "data": summary_data,
                "doc_url": doc_url,
            }
        except Exception as e:
            logger.error(f"机器人总结失败: {e}")
            return {
                "type": "text",
                "content": f"❌ 智能总结处理失败: {str(e)}",
            }
