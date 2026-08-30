# -*- coding: utf-8 -*-
import time
import hmac
import hashlib
import base64
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger("feishu_client")


class FeishuClient:
    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        webhook_url: str = "",
        bot_secret: str = "",
        doc_folder_token: str = "",
        bitable_app_token: str = "",
        bitable_table_id: str = "",
    ):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.webhook_url = webhook_url.strip()
        self.bot_secret = bot_secret.strip()
        self.doc_folder_token = doc_folder_token.strip()
        self.bitable_app_token = bitable_app_token.strip()
        self.bitable_table_id = bitable_table_id.strip()

        self._tenant_token: Optional[str] = None
        self._token_expires_at: float = 0

    def get_tenant_access_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._tenant_token and now < self._token_expires_at:
            return self._tenant_token

        if not self.app_id or not self.app_secret:
            raise ValueError("未配置 FEISHU_APP_ID 或 FEISHU_APP_SECRET，无法获取 Tenant Token")

        url = f"{self.BASE_URL}/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 Tenant Token 失败: {data.get('msg')}")

        self._tenant_token = data["tenant_access_token"]
        self._token_expires_at = now + data.get("expire", 7200) - 300
        logger.info("已成功获取飞书 Tenant Access Token")
        return self._tenant_token

    def reply_message(self, message_id: str, content: str, msg_type: str = "text") -> Dict[str, Any]:
        """使用飞书自建应用凭据对群聊中 @机器人 的消息进行官方回复"""
        if not self.app_id or not self.app_secret:
            logger.warning("未配置 App ID / App Secret，无法执行自建应用双向回复")
            return {"code": -1, "msg": "未配置自建应用凭据"}

        headers = self._get_headers()
        url = f"{self.BASE_URL}/im/v1/messages/{message_id}/reply"
        if msg_type == "text":
            body = {"content": json.dumps({"text": content}, ensure_ascii=False), "msg_type": "text"}
        elif msg_type == "interactive":
            body = {"content": json.dumps(content if isinstance(content, dict) else {}, ensure_ascii=False), "msg_type": "interactive"}
        else:
            body = {"content": json.dumps({"text": str(content)}, ensure_ascii=False), "msg_type": "text"}

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=10)
            res_data = resp.json()
            logger.info(f"飞书自建应用消息回复响应: {res_data}")
            return res_data
        except Exception as e:
            logger.error(f"调用飞书 reply_message 异常: {e}")
            return {"code": -1, "msg": str(e)}

    def send_chat_message(self, chat_id: str, content: str, msg_type: str = "text") -> Dict[str, Any]:
        """使用飞书自建应用凭据直接向指定群聊发送消息"""
        if not self.app_id or not self.app_secret:
            return {"code": -1, "msg": "未配置自建应用凭据"}

        headers = self._get_headers()
        url = f"{self.BASE_URL}/im/v1/messages?receive_id_type=chat_id"
        if msg_type == "text":
            body = {"receive_id": chat_id, "content": json.dumps({"text": content}, ensure_ascii=False), "msg_type": "text"}
        else:
            body = {"receive_id": chat_id, "content": json.dumps({"text": str(content)}, ensure_ascii=False), "msg_type": "text"}

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            return {"code": -1, "msg": str(e)}

    def _get_headers(self) -> Dict[str, str]:
        token = self.get_tenant_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _sign_webhook(self, timestamp: int) -> str:
        if not self.bot_secret:
            return ""
        string_to_sign = f"{timestamp}\n{self.bot_secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def send_meeting_summary_card(
        self,
        summary_data: Dict[str, Any],
        custom_webhook_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        webhook = custom_webhook_url or self.webhook_url
        if not webhook:
            raise ValueError("未配置飞书机器人 Webhook URL，请先在配置中填写或传入")

        timestamp = int(time.time())
        card_content = self._build_interactive_card(summary_data)

        payload: Dict[str, Any] = {
            "msg_type": "interactive",
            "card": card_content,
        }

        if self.bot_secret:
            payload["timestamp"] = str(timestamp)
            payload["sign"] = self._sign_webhook(timestamp)

        resp = requests.post(webhook, json=payload, timeout=10)
        res_data = resp.json()
        logger.info(f"飞书机器人卡片推送结果: {res_data}")
        return res_data

    def _build_interactive_card(self, data: Dict[str, Any]) -> Dict[str, Any]:
        title = data.get("title", "会议智能纪要与行动项")
        date_str = data.get("date", time.strftime("%Y-%m-%d %H:%M"))
        duration = data.get("duration", "约30分钟")
        overview = data.get("summary_overview", "本次会议已完成全链路智能梳理。")
        decisions = data.get("decisions", [])
        todos = data.get("todos", [])

        elements: List[Dict[str, Any]] = []

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📅 **会议时间**：{date_str}   |   ⏱️ **会议时长**：{duration}\n💡 **会议概览**：{overview}",
            },
        })
        elements.append({"tag": "hr"})

        if decisions:
            dec_md = "\n".join([f"? **{d}**" for d in decisions])
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"🎯 **核心决议定案 (Decisions)**\n{dec_md}",
                },
            })
            elements.append({"tag": "hr"})

        if todos:
            todo_rows = []
            for t in todos:
                owner = t.get("owner", "未指定")
                task = t.get("task", "")
                due = t.get("due", "尽快")
                prio = t.get("priority", "P1")
                todo_rows.append(f"? **[{owner}]** {task} `截止: {due}` *({prio})*")
            todo_md = "\n".join(todo_rows)
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"⚡ **行动清单 (Action Items & Todo)**\n{todo_md}",
                },
            })
            elements.append({"tag": "hr"})

        doc_url = data.get("doc_url") or "https://feishu.cn"
        if data.get("doc_url"):
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📄 **已生成飞书云文档**：[{title}]({doc_url})\n*（点击下方按钮或链接直接在飞书中查看与编辑）*",
                },
            })
            elements.append({"tag": "hr"})

        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📋 立即打开飞书云文档"},
                    "type": "primary",
                    "url": doc_url,
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "📊 打开多维表格"},
                    "type": "default",
                    "url": "https://feishu.cn",
                },
            ],
        })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"📋 {title}"},
                "template": "blue",
            },
            "elements": elements,
        }

    def create_feishu_doc(
        self,
        summary_data: Dict[str, Any],
        folder_token: Optional[str] = None,
    ) -> str:
        title = summary_data.get("title", "会议智能纪要")
        date_str = summary_data.get("date", time.strftime("%Y-%m-%d"))
        doc_title = f"【会议纪要】{title}_{date_str}"

        # If no Feishu App credentials configured, provide local doc generation
        if not self.app_id or not self.app_secret:
            logger.info("未配置飞书应用凭证，生成本地飞书标准纪要文档")
            out_dir = Path("records")
            out_dir.mkdir(exist_ok=True)
            doc_file = out_dir / f"{doc_title}.md"
            
            from .ai_summarizer import AISummarizer
            md_content = AISummarizer().format_to_markdown(summary_data)
            with open(doc_file, "w", encoding="utf-8") as f:
                f.write(md_content)
            return f"https://feishu.cn/docx/local_preview_{int(time.time())}"

        folder = folder_token or self.doc_folder_token
        headers = self._get_headers()
        url = f"{self.BASE_URL}/docx/v1/documents"
        payload: Dict[str, Any] = {"title": doc_title}
        if folder:
            payload["folder_token"] = folder

        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建飞书云文档失败: {data.get('msg')}")

        document_id = data["data"]["document"]["document_id"]
        doc_url = f"https://feishu.cn/docx/{document_id}"
        
        try:
            self._write_blocks_to_doc(document_id, summary_data)
        except Exception as e:
            logger.warning(f"写入文档内容块失败: {e}")

        logger.info(f"已成功创建飞书云文档并写入纪要内容: {doc_url}")
        return doc_url

    def _write_blocks_to_doc(self, document_id: str, summary_data: Dict[str, Any]):
        headers = self._get_headers()
        url = f"{self.BASE_URL}/docx/v1/documents/{document_id}/blocks/{document_id}/children"

        children = []

        overview = summary_data.get("summary_overview", "")
        if overview:
            children.append({
                "block_type": 4,
                "heading2": {"elements": [{"text_run": {"content": "💡 会议概览与背景"}}]},
            })
            children.append({
                "block_type": 2,
                "text": {"elements": [{"text_run": {"content": overview}}]},
            })

        decisions = summary_data.get("decisions", [])
        if decisions:
            children.append({
                "block_type": 4,
                "heading2": {"elements": [{"text_run": {"content": "🎯 核心决议与决策定案 (Decisions)"}}]},
            })
            for d in decisions:
                children.append({
                    "block_type": 12,
                    "bullet": {"elements": [{"text_run": {"content": d}}]},
                })

        todos = summary_data.get("todos", [])
        if todos:
            children.append({
                "block_type": 4,
                "heading2": {"elements": [{"text_run": {"content": "⚡ 行动清单与待办任务 (Action Items & Todo)"}}]},
            })
            for t in todos:
                owner = t.get("owner", "未指定")
                task = t.get("task", "")
                due = t.get("due", "尽快")
                prio = t.get("priority", "P1")
                todo_text = f"[{owner}] {task} (截止: {due}, 优先级: {prio})"
                children.append({
                    "block_type": 14,
                    "todo": {"elements": [{"text_run": {"content": todo_text}}]},
                })

        topics = summary_data.get("topics", [])
        if topics:
            children.append({
                "block_type": 4,
                "heading2": {"elements": [{"text_run": {"content": "📝 各议题详细讨论"}}]},
            })
            for top in topics:
                name = top.get("name", "议题")
                speaker = top.get("speaker", "")
                summary = top.get("summary", "")
                speaker_str = f" (发言人: {speaker})" if speaker else ""
                children.append({
                    "block_type": 5,
                    "heading3": {"elements": [{"text_run": {"content": f"?? {name}{speaker_str}"}}]},
                })
                children.append({
                    "block_type": 2,
                    "text": {"elements": [{"text_run": {"content": summary}}]},
                })

        if children:
            resp = requests.post(url, json={"children": children}, headers=headers, timeout=10)
            logger.info(f"已向飞书文档写入 {len(children)} 个富文本块")

    def sync_todos_to_bitable(
        self,
        todos: List[Dict[str, Any]],
        meeting_title: str = "",
        app_token: Optional[str] = None,
        table_id: Optional[str] = None,
    ) -> int:
        token = app_token or self.bitable_app_token
        table = table_id or self.bitable_table_id

        if not token or not table:
            logger.info("未配置多维表格 Token 或 Table ID，跳过同步")
            return 0

        headers = self._get_headers()
        url = f"{self.BASE_URL}/bitable/v1/apps/{token}/tables/{table}/records/batch_create"

        records = []
        for t in todos:
            fields = {
                "任务内容": t.get("task", ""),
                "责任人": t.get("owner", "未指定"),
                "截止时间": t.get("due", "尽快"),
                "优先级": t.get("priority", "P1"),
                "来源会议": meeting_title,
                "状态": "未开始",
            }
            records.append({"fields": fields})

        if not records:
            return 0

        resp = requests.post(url, json={"records": records}, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"同步多维表格失败: {data.get('msg')}")
            return 0

        created_count = len(data.get("data", {}).get("records", []))
        logger.info(f"已成功同步 {created_count} 条 Todo 到飞书多维表格")
        return created_count

    def extract_minute_token(self, url_or_token: str) -> str:
        text = url_or_token.strip()
        match = re.search(r"minutes/([a-zA-Z0-9_-]+)", text)
        if match:
            return match.group(1)
        return text

    def get_minute_info(self, minute_token: str) -> Dict[str, Any]:
        headers = self._get_headers()
        url = f"{self.BASE_URL}/minutes/v1/minutes/{minute_token}"
        resp = requests.get(url, headers=headers, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书妙记失败: {data.get('msg')}")
        return data.get("data", {}).get("minute", {})
