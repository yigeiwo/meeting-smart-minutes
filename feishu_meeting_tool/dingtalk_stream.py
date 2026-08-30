# -*- coding: utf-8 -*-
import time
import json
import asyncio
import logging
import threading
import urllib.request
from typing import Dict, Any, Optional

logger = logging.getLogger("dingtalk_stream")


class DingTalkStreamClient:
    """钉钉 Stream 模式长连接客户端 (无需公网 IP / 回调地址)"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DingTalkStreamClient, cls).__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self.client_id = ""
        self.client_secret = ""
        self.debug_log = False
        self.is_running = False
        self.status = "DISCONNECTED"  # DISCONNECTED, CONNECTING, CONNECTED, ERROR
        self.last_error = ""
        self._loop = None
        self._thread = None

    def configure(self, client_id: str, client_secret: str, debug_log: bool = False):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.debug_log = debug_log

    def start(self):
        if not self.client_id or not self.client_secret:
            self.status = "DISCONNECTED"
            return

        if self.is_running:
            return

        self.is_running = True
        self.status = "CONNECTING"
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"钉钉 Stream 模式监听已启动 (Client ID: {self.client_id})")

    def stop(self):
        self.is_running = False
        self.status = "DISCONNECTED"
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def test_connection(self, client_id: Optional[str] = None, client_secret: Optional[str] = None) -> Dict[str, Any]:
        """测试 DingTalk Stream 网关凭证有效性"""
        cid = (client_id or self.client_id).strip()
        sec = (client_secret or self.client_secret).strip()

        if not cid or not sec:
            return {"code": -1, "msg": "Client ID 或 Client Secret 为空"}

        try:
            url = "https://api.dingtalk.com/v1.0/gateway/connections/open"
            payload = json.dumps({
                "clientId": cid,
                "clientSecret": sec,
                "subscriptions": [
                    {"type": "EVENT", "topic": "*"},
                    {"type": "CALLBACK", "topic": "/v1.0/im/bot/messages/get"}
                ]
            }).encode("utf-8")

            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                endpoint = data.get("endpoint", "")
                ticket = data.get("ticket", "")
                if endpoint and ticket:
                    return {
                        "code": 0,
                        "msg": f"✔ 钉钉 Stream 网关认证成功！已获取长连接通道 (Endpoint: {endpoint[:25]}...)",
                        "data": {"endpoint": endpoint, "ticket": ticket}
                    }
                return {"code": 0, "msg": "✔ 钉钉网关验证通过", "raw": data}
        except Exception as e:
            err_msg = str(e)
            logger.error(f"钉钉 Stream 凭据测试失败: {err_msg}")
            return {"code": -1, "msg": f"钉钉认证失败: {err_msg} (请检查 Client ID 与 Client Secret 是否正确)"}

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._stream_worker())

    async def _stream_worker(self):
        import websockets

        while self.is_running:
            try:
                # 1. 向开放平台申请长连接 endpoint
                test_res = self.test_connection()
                if test_res.get("code") != 0:
                    self.status = "ERROR"
                    self.last_error = test_res.get("msg", "认证失败")
                    await asyncio.sleep(10)
                    continue

                endpoint = test_res["data"]["endpoint"]
                ticket = test_res["data"]["ticket"]
                ws_url = f"{endpoint}?ticket={ticket}"

                if self.debug_log:
                    logger.info(f"[DingTalk Stream] 正在连接 WebSocket: {endpoint}")

                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self.status = "CONNECTED"
                    logger.info("✔ 钉钉 Stream 模式 WebSocket 已连接，正在内网实时监听群聊消息...")

                    while self.is_running:
                        msg_raw = await ws.recv()
                        if self.debug_log:
                            logger.info(f"[DingTalk Stream 收到报文]: {msg_raw}")

                        try:
                            msg_obj = json.loads(msg_raw)
                            # 回应 ACK 心跳
                            spec_type = msg_obj.get("type", "")
                            msg_id = msg_obj.get("headers", {}).get("messageId", "")
                            
                            if spec_type == "SYSTEM" and msg_obj.get("headers", {}).get("topic") == "ping":
                                await ws.send(json.dumps({"code": 200, "headers": {"messageId": msg_id}, "message": "OK"}))
                                continue

                            # 处理群消息回调
                            if spec_type == "CALLBACK":
                                data_body = json.loads(msg_obj.get("data", "{}"))
                                text_content = data_body.get("text", {}).get("content", "").strip()
                                sender_nick = data_body.get("senderNick", "钉钉用户")
                                sender_id = data_body.get("senderStaffId", data_body.get("senderId", "ding_user"))
                                session_webhook = data_body.get("sessionWebhook", "")

                                from .robot_dispatcher import RobotDispatcher
                                reply_res = RobotDispatcher.handle_incoming_message(
                                    platform="dingtalk",
                                    sender_id=sender_id,
                                    sender_name=sender_nick,
                                    message_text=text_content,
                                    chat_id=data_body.get("conversationId", ""),
                                    raw_event=data_body
                                )

                                # 回复 ACK
                                await ws.send(json.dumps({
                                    "code": 200,
                                    "headers": {"messageId": msg_id, "contentType": "application/json"},
                                    "message": "OK",
                                    "data": json.dumps({
                                        "response": {
                                            "msgtype": "markdown",
                                            "markdown": {
                                                "title": "会议助手回复",
                                                "text": reply_res.get("content", "")
                                            }
                                        }
                                    })
                                }))

                                if self.debug_log:
                                    logger.info(f"[DingTalk Stream 回复成功]: {reply_res.get('content')[:60]}")

                        except Exception as parse_e:
                            logger.warning(f"处理钉钉 Stream 报文异常: {parse_e}")

            except Exception as e:
                self.status = "ERROR"
                self.last_error = str(e)
                if self.debug_log:
                    logger.warning(f"[DingTalk Stream] 连接中断: {e}，5秒后自动重连...")
                await asyncio.sleep(5)


def get_dingtalk_stream() -> DingTalkStreamClient:
    return DingTalkStreamClient()
