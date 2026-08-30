# -*- coding: utf-8 -*-
import os
import json
import time
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("wechat_clawbot")

SESSION_FILE = Path("records/wechat_session.json")


class WeChatClawBot:
    """微信 ClawBot / sillyGirl / GeWeChat 原生微信网关客户端 (100% 真实协议直连)"""

    def __init__(self, base_api_url: str = "http://127.0.0.1:2531", token: str = ""):
        self.base_api_url = base_api_url.rstrip("/")
        self.token = token
        self.app_id = ""
        self.uuid = ""
        self.qr_data = ""
        self.status = "DISCONNECTED"
        self.logged_user: Dict[str, Any] = {}
        self._load_session()

    def _load_session(self):
        try:
            if SESSION_FILE.exists():
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                if data.get("logged_in") and data.get("wxid"):
                    self.logged_user = data
                    self.status = "LOGGED_IN"
                    self.app_id = data.get("app_id", "")
                    logger.info(f"✔ 成功恢复微信已登录真实会话: {data.get('nickname')} ({data.get('wxid')})")
        except Exception as e:
            logger.warning(f"读取微信会话失败: {e}")

    def _save_session(self):
        try:
            SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_FILE.write_text(json.dumps(self.logged_user, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"保存微信会话失败: {e}")

    def get_login_qr(self, custom_api_url: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
        return self.fetch_qr_code(base_api_url=custom_api_url, token=token)

    def fetch_qr_code(self, base_api_url: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
        """向真实 GeWeChat / ClawBot / sillyGirl 微信网关请求真实登录二维码"""
        if base_api_url:
            self.base_api_url = base_api_url.rstrip("/")
        if token is not None:
            self.token = token

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GEWE-TOKEN"] = self.token
            headers["Authorization"] = f"Bearer {self.token}"

        # 1. 尝试调用真实 GeWeChat / ClawBot 接口获取二维码
        try:
            url = f"{self.base_api_url}/v2/api/login/getLoginQrCode"
            payload = json.dumps({"appId": self.app_id}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ret") == 200 or data.get("code") == 200:
                    ret_data = data.get("data", {})
                    self.app_id = ret_data.get("appId", "")
                    self.uuid = ret_data.get("uuid", "")
                    self.qr_data = ret_data.get("qrData", "")
                    self.status = "WAITING_SCAN"
                    return {
                        "code": 0,
                        "uuid": self.uuid,
                        "qr_b64": ret_data.get("qrImgBase64", ""),
                        "msg": "请使用手机微信扫描真实登录二维码",
                        "status": self.status
                    }
                else:
                    return {"code": -1, "msg": f"微信服务端返回: {data.get('msg', '获取二维码失败')}"}
        except Exception as e:
            logger.warning(f"直连微信网关接口 {self.base_api_url} 异常: {e}")
            return {
                "code": -1,
                "msg": f"无法连接到微信网关 ({self.base_api_url}): {str(e)}。请确认您的 ClawBot / GeWeChat 微信后端服务已真实启动！"
            }

    def check_login_status(self) -> Dict[str, Any]:
        """向真实微信网关轮询当前登录与扫码状态"""
        if self.status == "LOGGED_IN" and self.logged_user.get("wxid"):
            return {
                "code": 0,
                "state": "LOGGED_IN",
                "wxid": self.logged_user.get("wxid"),
                "nickname": self.logged_user.get("nickname"),
                "avatar_url": self.logged_user.get("avatar_url", ""),
                "msg": "已在线登录"
            }

        if not self.uuid and not self.app_id:
            return {"code": 0, "state": "WAITING_SCAN", "msg": "未获取二维码"}

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GEWE-TOKEN"] = self.token
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            url = f"{self.base_api_url}/v2/api/login/checkLogin"
            payload = json.dumps({"appId": self.app_id, "uuid": self.uuid}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ret") == 200 or data.get("code") == 200:
                    ret_data = data.get("data", {})
                    status_code = ret_data.get("status", 0)
                    # 0: 未扫码, 1: 已扫码待确认, 2: 登录成功, 3: 二维码失效
                    if status_code == 1:
                        self.status = "SCANNED"
                        return {"code": 0, "state": "SCANNED", "msg": "✔ 手机已扫码，请在手机上点击确认登录"}
                    elif status_code == 2:
                        self.status = "LOGGED_IN"
                        login_info = ret_data.get("loginInfo", {})
                        self.logged_user = {
                            "logged_in": True,
                            "app_id": self.app_id,
                            "wxid": login_info.get("wxid", ""),
                            "nickname": login_info.get("nickName", "微信用户"),
                            "avatar_url": login_info.get("headImgUrl", ""),
                            "login_time": time.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        self._save_session()
                        return {
                            "code": 0,
                            "state": "LOGGED_IN",
                            "wxid": self.logged_user["wxid"],
                            "nickname": self.logged_user["nickname"],
                            "avatar_url": self.logged_user["avatar_url"],
                            "msg": "登录成功"
                        }
                    elif status_code == 3:
                        self.status = "EXPIRED"
                        return {"code": 0, "state": "EXPIRED", "msg": "二维码已过期，请刷新"}
        except Exception as e:
            logger.debug(f"轮询真实微信登录状态异常: {e}")

        return {"code": 0, "state": self.status, "msg": "等待扫码中"}

    def logout(self):
        """退出当前真实微信登录并清空会话"""
        self.status = "DISCONNECTED"
        self.uuid = ""
        self.logged_user = {}
        if SESSION_FILE.exists():
            try:
                SESSION_FILE.unlink()
            except Exception:
                pass
        return {"code": 0, "msg": "微信已安全退出"}

    def send_message(self, target_wxid: str, content: str) -> Dict[str, Any]:
        """向微信真实好友或微信群真实发送消息"""
        if not target_wxid:
            return {"code": -1, "msg": "未指定接收目标微信号或群ID (Target Wxid)"}

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-GEWE-TOKEN"] = self.token
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            url = f"{self.base_api_url}/v2/api/message/postText"
            payload = json.dumps({
                "appId": self.app_id,
                "toWxid": target_wxid,
                "content": content
            }).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("ret") == 200 or data.get("code") == 200:
                    return {"code": 0, "msg": f"✔ 微信消息已真实发送至 {target_wxid}", "raw": data}
                return {"code": -1, "msg": f"微信发送失败: {data.get('msg', data)}"}
        except Exception as e:
            return {"code": -1, "msg": f"连接微信发送接口异常: {str(e)} (请确认微信网关 {self.base_api_url} 正常运行)"}


_global_wechat_bot: Optional[WeChatClawBot] = None

def get_wechat_bot() -> WeChatClawBot:
    global _global_wechat_bot
    if _global_wechat_bot is None:
        _global_wechat_bot = WeChatClawBot()
    return _global_wechat_bot
