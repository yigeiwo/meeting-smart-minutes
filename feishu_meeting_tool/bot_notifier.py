# -*- coding: utf-8 -*-
import time
import json
import hmac
import base64
import hashlib
import logging
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional

logger = logging.getLogger("bot_notifier")

DEFAULT_BOT_CHANNELS = [
    {
        "id": "wechat_clawbot",
        "name": "微信 ClawBot (原生微信)",
        "type": "wechat_clawbot",
        "icon": "bi-wechat",
        "color": "success",
        "enabled": False,
        "base_api_url": "http://127.0.0.1:2531",
        "target_wxid": "",
        "token": "",
        "description": "基于 sillyGirl 微信 ClawBot/iLink/GeWe 协议，支持微信扫码登录、好友与微信群消息双向收发",
    },
    {
        "id": "feishu",
        "name": "飞书群机器人",
        "type": "feishu",
        "icon": "bi-feather",
        "color": "primary",
        "enabled": True,
        "webhook_url": "",
        "secret": "",
        "at_all": False,
        "description": "支持飞书富文本互动卡片、加签鉴权、@所有人 与云文档按钮直接跳转",
    },
    {
        "id": "dingtalk",
        "name": "钉钉机器人",
        "type": "dingtalk",
        "icon": "bi-send-fill",
        "color": "info",
        "enabled": False,
        "client_id": "",
        "client_secret": "",
        "debug_log": False,
        "webhook_url": "",
        "secret": "",
        "is_at_all": False,
        "at_mobiles": "",
        "description": "钉钉开放平台应用的 Client ID（原 AppKey）。适配器使用 Stream 模式，不需要公网回调地址。",
    },
    {
        "id": "wecom",
        "name": "企业微信机器人",
        "type": "wecom",
        "icon": "bi-chat-dots-fill",
        "color": "success",
        "enabled": False,
        "webhook_url": "",
        "mentioned_list": "",
        "mentioned_mobile_list": "",
        "corp_id": "",
        "agent_id": "",
        "app_secret": "",
        "description": "支持企业微信群机器人 Markdown 消息、@成员 与手机号提醒，支持自建应用双向交互",
    },
    {
        "id": "telegram",
        "name": "Telegram 机器人",
        "type": "telegram",
        "icon": "bi-telegram",
        "color": "primary",
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
        "api_base": "https://api.telegram.org",
        "proxy_url": "",
        "description": "支持 Telegram Bot API (sendMessage) 与 MarkdownV2 / HTML 消息，支持国内反代网关",
    },
    {
        "id": "onebot",
        "name": "QQ 机器人",
        "type": "onebot",
        "icon": "bi-tencent-qq",
        "color": "danger",
        "enabled": False,
        "protocol": "onebot",
        "http_api_url": "http://127.0.0.1:3000",
        "access_token": "",
        "target_type": "group",
        "target_id": "",
        "master_id": "",
        "app_id": "",
        "app_secret": "",
        "debug_log": False,
        "description": "支持 OneBot v11 协议 (NapCat/Lagrange)、反向 WebSocket 长连接与 QQ 开放平台官方应用",
    },
    {
        "id": "pushdeer",
        "name": "PushDeer (无界推送)",
        "type": "pushdeer",
        "icon": "bi-send-check-fill",
        "color": "primary",
        "enabled": False,
        "pushkey": "",
        "endpoint": "https://api2.pushdeer.com",
        "description": "轻量开源无界推送服务，支持 iOS / Android / Mac 客户端与微信免装 App 接收",
    },
    {
        "id": "pushplus",
        "name": "PushPlus (微信推送)",
        "type": "pushplus",
        "icon": "bi-wechat",
        "color": "success",
        "enabled": False,
        "token": "",
        "topic": "",
        "channel": "wechat",
        "description": "通过微信公众号 PushPlus 实时推送会议通知与决议 (支持群发 Topic)",
    },
    {
        "id": "serverchan",
        "name": "Server酱 (方糖/Turbo)",
        "type": "serverchan",
        "icon": "bi-bell-fill",
        "color": "warning",
        "enabled": False,
        "sendkey": "",
        "channel": "9",
        "description": "支持 Server酱 Turbo 版微信公众号、企业微信应用等多通道推送",
    },
    {
        "id": "bark",
        "name": "Bark (iOS 即时推送)",
        "type": "bark",
        "icon": "bi-apple",
        "color": "dark",
        "enabled": False,
        "device_key": "",
        "server_url": "https://api.day.app",
        "group": "会议纪要",
        "sound": "minuet",
        "level": "active",
        "description": "支持 iPhone/iPad 原生弹窗通知，带提示音、重要时效性级别与云文档跳转链接",
    },
    {
        "id": "discord",
        "name": "Discord 机器人",
        "type": "discord",
        "icon": "bi-discord",
        "color": "primary",
        "enabled": False,
        "webhook_url": "",
        "username": "AI 会议助理",
        "avatar_url": "https://raw.githubusercontent.com/smallfawn/sillyGirl/master/logo.png",
        "description": "支持 Discord Webhook 频道富文本 Embed 结构化会议纪要卡片直推",
    },
    {
        "id": "custom",
        "name": "自定义 Webhook",
        "type": "custom",
        "icon": "bi-code-slash",
        "color": "secondary",
        "enabled": False,
        "webhook_url": "",
        "http_method": "POST",
        "auth_header": "",
        "description": "支持向任意第三方标准 HTTP POST/PUT 接口发送 JSON 格式会议纪要",
    },
]


class BotNotifier:
    """多机器人多渠道推送与安全鉴权引擎 (参考 sillyGirl 多适配器架构)"""

    @classmethod
    def format_summary_markdown(cls, summary_data: Dict[str, Any], doc_url: str = "") -> str:
        title = summary_data.get("title", "会议智能纪要")
        date_str = summary_data.get("date", time.strftime("%Y-%m-%d %H:%M"))
        duration = summary_data.get("duration", "约30分钟")
        participants = summary_data.get("participants", "全员")
        overview = summary_data.get("summary_overview", "")
        decisions = summary_data.get("decisions", [])
        todos = summary_data.get("todos", [])

        md = f"### 📋 {title}\n"
        md += f"**📅 时间**: {date_str} | **⏱️ 时长**: {duration}\n"
        md += f"**👥 参会人**: {participants}\n\n"

        if overview:
            md += f"**💡 会议概览**: {overview}\n\n"

        if decisions:
            md += "**🎯 核心决议:**\n"
            for d in decisions:
                md += f"- **{d}**\n"
            md += "\n"

        if todos:
            md += "**✅ 行动项待办 (Todo):**\n"
            for t in todos:
                task = t.get("task", "")
                owner = t.get("owner", "待定")
                due = t.get("due", "本周")
                prio = t.get("priority", "P1")
                md += f"- [{prio}] **{task}** (👤 {owner} | ⏳ {due})\n"
            md += "\n"

        if doc_url and doc_url.startswith("http"):
            md += f"👉 **[点击查阅完整飞书云文档]({doc_url})**\n"

        return md

    @classmethod
    def send_to_channel(cls, channel: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str = "") -> Dict[str, Any]:
        ch_type = channel.get("type", "").lower()

        try:
            if ch_type == "wechat_clawbot":
                return cls._send_wechat_clawbot(channel, summary_data, doc_url)
            elif ch_type == "feishu":
                return cls._send_feishu(channel, summary_data, doc_url)
            elif ch_type == "dingtalk":
                return cls._send_dingtalk(channel, summary_data, doc_url)
            elif ch_type == "wecom":
                return cls._send_wecom(channel, summary_data, doc_url)
            elif ch_type == "telegram":
                return cls._send_telegram(channel, summary_data, doc_url)
            elif ch_type == "onebot":
                return cls._send_onebot(channel, summary_data, doc_url)
            elif ch_type == "pushdeer":
                return cls._send_pushdeer(channel, summary_data, doc_url)
            elif ch_type == "pushplus":
                return cls._send_pushplus(channel, summary_data, doc_url)
            elif ch_type == "serverchan":
                return cls._send_serverchan(channel, summary_data, doc_url)
            elif ch_type == "bark":
                return cls._send_bark(channel, summary_data, doc_url)
            elif ch_type == "discord":
                return cls._send_discord(channel, summary_data, doc_url)
            elif ch_type == "custom":
                return cls._send_custom_webhook(channel, summary_data, doc_url)
            else:
                return {"code": -1, "msg": f"未知渠道类型: {ch_type}"}
        except Exception as e:
            logger.error(f"推送渠道 [{channel.get('name')}] 失败: {e}")
            return {"code": -1, "msg": f"推送异常: {str(e)}"}

    @classmethod
    def send_test_message(cls, channel: Dict[str, Any]) -> Dict[str, Any]:
        test_summary = {
            "title": "🎉 机器人多渠道推送测试通知",
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": "1分钟",
            "participants": "系统管理员, 测试员工",
            "summary_overview": f"本通知为【{channel.get('name')}】渠道连通性与签名鉴权验证消息，收到此消息表明您的通道已配置就绪！",
            "decisions": ["机器人网关连通性正常", "多渠道广播调度链路畅通"],
            "todos": [{"task": "在实际会议中享受一键多渠道纪要分发", "owner": "全体员工", "due": "随时", "priority": "P0"}],
            "doc_url": "https://feishu.cn",
        }
        return cls.send_to_channel(channel, test_summary, "https://feishu.cn")

    @classmethod
    def broadcast(cls, channels: List[Dict[str, Any]], summary_data: Dict[str, Any], doc_url: str = "") -> List[Dict[str, Any]]:
        results = []
        for ch in channels:
            if ch.get("enabled"):
                res = cls.send_to_channel(ch, summary_data, doc_url)
                results.append({
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "type": ch.get("type"),
                    "success": (res.get("code") == 0),
                    "error": res.get("msg") if res.get("code") != 0 else "",
                    "result": res,
                })
        return results

    # ================= 各种渠道具体推送实现 =================

    @classmethod
    def _send_wechat_clawbot(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        from .wechat_clawbot import get_wechat_bot
        bot = get_wechat_bot()
        target_wxid = ch.get("target_wxid", "").strip() or ch.get("target_id", "").strip()
        if not target_wxid:
            return {"code": -1, "msg": "未配置目标微信号/群ID (target_wxid)"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)
        content = f"📋 【{title}】\n\n" + md_text
        return bot.send_message(target_wxid, content)

    @classmethod
    def _send_feishu(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        webhook_url = ch.get("webhook_url", "").strip()
        if not webhook_url:
            return {"code": -1, "msg": "未配置飞书群机器人 Webhook URL"}

        secret = ch.get("secret", "").strip()
        from .feishu_client import FeishuClient
        feishu = FeishuClient(webhook_url=webhook_url, bot_secret=secret)
        actual_doc = doc_url or summary_data.get("doc_url", "")
        if actual_doc:
            summary_data["doc_url"] = actual_doc
        res = feishu.send_meeting_summary_card(summary_data)
        return {"code": 0, "msg": "飞书机器人推送成功", "raw": res}

    @classmethod
    def _send_dingtalk(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        webhook_url = ch.get("webhook_url", "").strip()
        if not webhook_url:
            # 如果配置了 Stream 模式凭据，提示已通过 Stream 模式双向托管
            if ch.get("client_id") and ch.get("client_secret"):
                return {"code": 0, "msg": "钉钉 Stream 模式运行中，群内消息已双向长连接监听"}
            return {"code": -1, "msg": "未配置钉钉群机器人 Webhook URL 或 Client ID"}

        secret = ch.get("secret", "").strip()
        final_url = webhook_url

        if secret:
            timestamp = str(round(time.time() * 1000))
            secret_enc = secret.encode("utf-8")
            string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
            hmac_code = hmac.new(secret_enc, string_to_sign, digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            sep = "&" if "?" in webhook_url else "?"
            final_url = f"{webhook_url}{sep}timestamp={timestamp}&sign={sign}"

        md_text = cls.format_summary_markdown(summary_data, doc_url)
        title = summary_data.get("title", "会议智能纪要")
        
        at_mobiles = [m.strip() for m in str(ch.get("at_mobiles", "")).split(",") if m.strip()]
        is_at_all = bool(ch.get("is_at_all", False))

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n" + md_text
            },
            "at": {
                "atMobiles": at_mobiles,
                "isAtAll": is_at_all
            }
        }
        res = cls._http_post_json(final_url, payload)
        if res.get("errcode") == 0:
            return {"code": 0, "msg": "钉钉群机器人推送成功"}
        return {"code": -1, "msg": f"钉钉返回: {res.get('errmsg', res)}"}

    @classmethod
    def _send_wecom(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        webhook_url = ch.get("webhook_url", "").strip()
        if not webhook_url:
            return {"code": -1, "msg": "未配置企业微信机器人 Webhook URL"}

        md_text = cls.format_summary_markdown(summary_data, doc_url)
        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": md_text
            }
        }
        mentioned = [x.strip() for x in str(ch.get("mentioned_list", "")).split(",") if x.strip()]
        mobiles = [x.strip() for x in str(ch.get("mentioned_mobile_list", "")).split(",") if x.strip()]
        if mentioned:
            payload["markdown"]["mentioned_list"] = mentioned
        if mobiles:
            payload["markdown"]["mentioned_mobile_list"] = mobiles

        res = cls._http_post_json(webhook_url, payload)
        if res.get("errcode") == 0:
            return {"code": 0, "msg": "企业微信机器人推送成功"}
        return {"code": -1, "msg": f"企业微信返回: {res.get('errmsg', res)}"}

    @classmethod
    def _send_telegram(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        bot_token = ch.get("bot_token", "").strip()
        chat_id = ch.get("chat_id", "").strip()
        api_base = (ch.get("api_base") or "https://api.telegram.org").rstrip("/")

        if not bot_token or not chat_id:
            return {"code": -1, "msg": "未配置 Telegram Bot Token 或 Chat ID"}

        title = summary_data.get("title", "会议智能纪要")
        date_str = summary_data.get("date", "")
        overview = summary_data.get("summary_overview", "")
        decisions = summary_data.get("decisions", [])
        todos = summary_data.get("todos", [])

        msg_html = f"📋 <b>{title}</b>\n"
        msg_html += f"📅 <b>时间</b>: {date_str}\n\n"
        if overview:
            msg_html += f"💡 <b>会议概览</b>:\n{overview}\n\n"
        if decisions:
            msg_html += "🎯 <b>核心决议</b>:\n"
            for d in decisions:
                msg_html += f"• {d}\n"
            msg_html += "\n"
        if todos:
            msg_html += "✅ <b>行动项待办</b>:\n"
            for t in todos:
                msg_html += f"• [{t.get('priority', 'P1')}] {t.get('task')} (👤 {t.get('owner')} | ⏳ {t.get('due')})\n"
            msg_html += "\n"
        if doc_url and doc_url.startswith("http"):
            msg_html += f'👉 <a href="{doc_url}">点击查阅完整飞书云文档</a>'

        url = f"{api_base}/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        res = cls._http_post_json(url, payload)
        if res.get("ok"):
            return {"code": 0, "msg": "Telegram 消息推送成功"}
        return {"code": -1, "msg": f"Telegram 返回: {res.get('description', res)}"}

    @classmethod
    def _send_onebot(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        api_url = (ch.get("http_api_url") or "http://127.0.0.1:3000").rstrip("/")
        token = ch.get("access_token", "").strip()
        target_type = ch.get("target_type", "group")
        target_id = str(ch.get("target_id", "")).strip()

        if not target_id:
            return {"code": -1, "msg": "未配置 QQ 群号或好友 QQ 号 (Target ID)"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        if target_type == "group":
            endpoint = f"{api_url}/send_group_msg"
            payload = {"group_id": int(target_id) if target_id.isdigit() else target_id, "message": f"【{title}】\n\n" + md_text}
        else:
            endpoint = f"{api_url}/send_private_msg"
            payload = {"user_id": int(target_id) if target_id.isdigit() else target_id, "message": f"【{title}】\n\n" + md_text}

        res = cls._http_post_json(endpoint, payload, headers=headers)
        if res.get("status") == "ok" or res.get("retcode") == 0:
            return {"code": 0, "msg": "QQ / OneBot 消息推送成功"}
        return {"code": -1, "msg": f"OneBot 返回: {res.get('msg', res)}"}

    @classmethod
    def _send_pushdeer(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        pushkey = ch.get("pushkey", "").strip()
        endpoint = (ch.get("endpoint") or "https://api2.pushdeer.com").rstrip("/")
        if not pushkey:
            return {"code": -1, "msg": "未配置 PushDeer PushKey"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)
        url = f"{endpoint}/message/push"
        payload = {
            "pushkey": pushkey,
            "text": title,
            "desp": md_text,
            "type": "markdown"
        }
        res = cls._http_post_json(url, payload)
        if res.get("code") == 0 or res.get("content", {}).get("result"):
            return {"code": 0, "msg": "PushDeer 推送成功"}
        return {"code": -1, "msg": f"PushDeer 返回: {res.get('error', res)}"}

    @classmethod
    def _send_pushplus(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        token = ch.get("token", "").strip()
        if not token:
            return {"code": -1, "msg": "未配置 PushPlus Token"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)

        payload = {
            "token": token,
            "title": title[:30],
            "content": md_text,
            "template": "markdown",
            "channel": ch.get("channel", "wechat")
        }
        if ch.get("topic"):
            payload["topic"] = ch.get("topic")

        res = cls._http_post_json("https://www.pushplus.plus/send", payload)
        if res.get("code") == 200:
            return {"code": 0, "msg": "PushPlus 微信推送成功"}
        return {"code": -1, "msg": f"PushPlus 返回: {res.get('msg', res)}"}

    @classmethod
    def _send_serverchan(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        sendkey = ch.get("sendkey", "").strip()
        if not sendkey:
            return {"code": -1, "msg": "未配置 Server酱 SendKey"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {
            "title": title[:30],
            "desp": md_text,
            "channel": ch.get("channel", "9")
        }
        res = cls._http_post_json(url, payload)
        if res.get("code") == 0 or res.get("data", {}).get("error") == "SUCCESS":
            return {"code": 0, "msg": "Server酱推送成功"}
        return {"code": -1, "msg": f"Server酱返回: {res.get('message', res)}"}

    @classmethod
    def _send_bark(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        device_key = ch.get("device_key", "").strip()
        server_url = (ch.get("server_url") or "https://api.day.app").rstrip("/")

        if not device_key:
            return {"code": -1, "msg": "未配置 Bark Device Key"}

        title = summary_data.get("title", "会议智能纪要")
        overview = summary_data.get("summary_overview", "")
        decisions_cnt = len(summary_data.get("decisions", []))
        todos_cnt = len(summary_data.get("todos", []))
        body = f"{overview}\n已提取 {decisions_cnt} 项决议，{todos_cnt} 项待办"

        payload = {
            "title": title,
            "body": body,
            "group": ch.get("group", "会议纪要"),
            "sound": ch.get("sound", "minuet"),
            "level": ch.get("level", "active"),
            "icon": "https://feishu.cn/favicon.ico"
        }
        if doc_url and doc_url.startswith("http"):
            payload["url"] = doc_url

        url = f"{server_url}/{device_key}"
        res = cls._http_post_json(url, payload)
        if res.get("code") == 200:
            return {"code": 0, "msg": "Bark iOS 推送成功"}
        return {"code": -1, "msg": f"Bark 返回: {res.get('message', res)}"}

    @classmethod
    def _send_discord(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        webhook_url = ch.get("webhook_url", "").strip()
        if not webhook_url:
            return {"code": -1, "msg": "未配置 Discord Webhook URL"}

        title = summary_data.get("title", "会议智能纪要")
        md_text = cls.format_summary_markdown(summary_data, doc_url)
        payload = {
            "username": ch.get("username", "AI 会议助手"),
            "avatar_url": ch.get("avatar_url", ""),
            "embeds": [
                {
                    "title": f"📋 {title}",
                    "description": md_text[:2000],
                    "color": 5814783,
                    "url": doc_url if doc_url.startswith("http") else None
                }
            ]
        }
        res = cls._http_post_json(webhook_url, payload)
        return {"code": 0, "msg": "Discord 卡片推送成功", "raw": res}

    @classmethod
    def _send_custom_webhook(cls, ch: Dict[str, Any], summary_data: Dict[str, Any], doc_url: str) -> Dict[str, Any]:
        webhook_url = ch.get("webhook_url", "").strip()
        if not webhook_url:
            return {"code": -1, "msg": "未配置自定义 Webhook URL"}

        headers = {}
        auth_hdr = ch.get("auth_header", "").strip()
        if auth_hdr:
            if ":" in auth_hdr:
                k, v = auth_hdr.split(":", 1)
                headers[k.strip()] = v.strip()
            else:
                headers["Authorization"] = auth_hdr

        method = ch.get("http_method", "POST").upper()
        payload = {
            "event": "meeting_summary",
            "timestamp": time.time(),
            "doc_url": doc_url or summary_data.get("doc_url", ""),
            "data": summary_data,
        }
        res = cls._http_post_json(webhook_url, payload, headers=headers, method=method)
        return {"code": 0, "msg": "自定义 Webhook 发送成功", "raw": res}

    @classmethod
    def _http_post_json(cls, url: str, data: Dict[str, Any], headers: Optional[Dict[str, str]] = None, method: str = "POST") -> Dict[str, Any]:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        req_headers = {"Content-Type": "application/json; charset=utf-8"}
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(raw)
            except Exception:
                return {"status_code": resp.status, "raw": raw}
