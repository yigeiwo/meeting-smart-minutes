# -*- coding: utf-8 -*-
import json
import time
import socket
import struct
import base64
import os
import threading
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("card_bridge")

FONT_PATH = None
for f in ["msyh.ttc", "simhei.ttf", "simsun.ttc", "arial.ttf"]:
    p = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", f)
    if os.path.exists(p):
        FONT_PATH = p
        break


def get_font(size: int = 14) -> ImageFont.FreeTypeFont:
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception:
            pass
    return ImageFont.load_default()


class CardBridge:
    def __init__(self, host: str = "0.0.0.0", port: int = 5566):
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.clients: List[socket.socket] = []
        self._lock = threading.Lock()
        self.is_running = False

        # State machine (DISCONNECTED, IDLE, RECORDING, PROCESSING, SUMMARIZED)
        self.state = "DISCONNECTED"
        self.record_start_time = 0
        self.record_duration = 0
        self.battery_soc = None
        self.current_summary: Optional[Dict[str, Any]] = None
        self.current_todo_index = 0
        self._timer_thread: Optional[threading.Thread] = None

        # Callbacks
        self.on_record_start_callbacks: List[Callable] = []
        self.on_record_stop_callbacks: List[Callable] = []
        self.on_request_summary_callbacks: List[Callable] = []

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            logger.info(f"AI Passport 硬件桥接服务已启动: tcp://{self.host}:{self.port}")
            threading.Thread(target=self._accept_loop, daemon=True).start()
        except Exception as e:
            logger.warning(f"无法绑定端口 {self.port} (可能已被占用): {e}")

    def stop(self):
        self.is_running = False
        with self._lock:
            for c in self.clients:
                try:
                    c.close()
                except Exception:
                    pass
            self.clients.clear()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None

    def _accept_loop(self):
        while self.is_running and self.server_socket:
            try:
                client_sock, addr = self.server_socket.accept()
                logger.info(f"AI Passport 真实硬件卡片/模拟器已连接: {addr}")
                with self._lock:
                    self.clients.append(client_sock)
                
                self.state = "IDLE"
                self.battery_soc = 85
                self.render_and_send_frame()

                threading.Thread(
                    target=self._client_handler, args=(client_sock, addr), daemon=True
                ).start()
            except Exception as e:
                if self.is_running:
                    logger.error(f"桥接服务 accept 异常: {e}")
                break

    def _client_handler(self, client_sock: socket.socket, addr):
        buffer = ""
        client_sock.settimeout(120.0)
        try:
            while self.is_running:
                data = client_sock.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._process_message(line, client_sock)
        except Exception as e:
            logger.debug(f"客户端 {addr} 断开: {e}")
        finally:
            with self._lock:
                if client_sock in self.clients:
                    self.clients.remove(client_sock)
                if len(self.clients) == 0:
                    self.state = "DISCONNECTED"
                    self.battery_soc = None
            try:
                client_sock.close()
            except Exception:
                pass
            logger.info(f"AI Passport 硬件卡片已断开: {addr}")

    def _process_message(self, message_str: str, client_sock: socket.socket):
        try:
            msg = json.loads(message_str)
            msg_type = msg.get("type") or msg.get("event") or msg.get("cmd")

            if msg_type == "button":
                name = msg.get("name")
                state = msg.get("state")
                if state == "down":
                    self._handle_button_press(name)

            elif msg_type == "battery":
                self.battery_soc = msg.get("soc", 85)
                self.render_and_send_frame()

            elif msg_type == "record_start":
                self._start_recording()
            elif msg_type == "record_stop":
                self._stop_recording()
            elif msg_type == "fetch_summary":
                self.render_and_send_frame()
            elif msg_type == "ping":
                self.send_to_client(client_sock, {"event": "pong", "time": time.time()})

        except Exception as e:
            logger.error(f"\u5904\u7406\u5361\u7247\u6d88\u606f\u5931\u8d25: {e}, \u539f\u59cb\u6d88\u606f: {message_str}")

    def _handle_button_press(self, btn_name: str):
        logger.info(f"\u6536\u5230\u6309\u952e\u89e6\u53d1: {btn_name}, \u5f53\u524d\u72b6\u6001: {self.state}")
        if btn_name == "OK":
            if self.state in ["IDLE", "SUMMARIZED"]:
                self._start_recording()
            elif self.state == "RECORDING":
                self._stop_recording()
        elif btn_name == "UP":
            if self.current_summary and self.current_summary.get("todos"):
                self.current_todo_index = max(0, self.current_todo_index - 1)
                self.render_and_send_frame()
        elif btn_name == "DOWN":
            if self.current_summary and self.current_summary.get("todos"):
                max_idx = len(self.current_summary["todos"]) - 1
                self.current_todo_index = min(max_idx, self.current_todo_index + 1)
                self.render_and_send_frame()

    def _start_recording(self):
        self.state = "RECORDING"
        self.record_start_time = time.time()
        self.render_and_send_frame()

        for cb in self.on_record_start_callbacks:
            try:
                cb()
            except Exception:
                pass

        if not self._timer_thread or not self._timer_thread.is_alive():
            self._timer_thread = threading.Thread(target=self._recording_timer_loop, daemon=True)
            self._timer_thread.start()

    def _recording_timer_loop(self):
        while self.is_running and self.state == "RECORDING":
            time.sleep(1.0)
            if self.state == "RECORDING":
                self.render_and_send_frame()

    def _stop_recording(self):
        if self.state != "RECORDING":
            return
        self.record_duration = int(time.time() - self.record_start_time)
        self.state = "PROCESSING"
        self.render_and_send_frame()

        threading.Thread(target=self._run_auto_summary_pipeline, daemon=True).start()

    def _run_auto_summary_pipeline(self):
        try:
            from .config import get_config
            from .ai_summarizer import AISummarizer
            from .audio_pipeline import AudioPipeline
            from .feishu_client import FeishuClient

            cfg = get_config()
            pipeline = AudioPipeline(
                records_dir=cfg.records_dir,
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url,
            )
            records_path = Path(cfg.records_dir)
            wav_files = sorted(list(records_path.glob("*.wav")), key=lambda p: p.stat().st_mtime, reverse=True)
            if not wav_files:
                raise FileNotFoundError("未在 records 目录找到卡片录音音频文件 (.wav)")
            target_wav = wav_files[0]
            logger.info(f"正在转写卡片真实录音文件: {target_wav.name}")
            transcript = pipeline.transcribe(str(target_wav))

            summarizer = AISummarizer(
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url,
                model=cfg.llm_model,
                temperature=cfg.llm_temperature,
            )
            summary_data = summarizer.summarize(transcript, scenario=cfg.default_scenario)

            # 1. 自动生成飞书云文档 (Docx)
            feishu = FeishuClient(
                app_id=cfg.feishu_app_id,
                app_secret=cfg.feishu_app_secret,
                webhook_url=cfg.feishu_bot_webhook_url,
                bot_secret=cfg.feishu_bot_secret,
                doc_folder_token=cfg.feishu_doc_folder_token,
            )
            try:
                doc_url = feishu.create_feishu_doc(summary_data)
                summary_data["doc_url"] = doc_url
                logger.info(f"✔ 卡片录音结束，已自动生成飞书云文档: {doc_url}")
            except Exception as e:
                logger.warning(f"自动创建飞书文档异常: {e}")
                summary_data["doc_url"] = "https://feishu.cn"

            # 2. 自动向所有启用的机器人渠道广播会议纪要通知卡片（包含文档链接、决议与待办）
            try:
                from .bot_notifier import BotNotifier
                bot_res = BotNotifier.broadcast(cfg.bot_channels, summary_data, doc_url=doc_url)
                logger.info(f"✔ 卡片录音结束，已自动向启用的多机器人渠道广播通知: {bot_res}")
            except Exception as e:
                logger.warning(f"自动广播多机器人通知失败: {e}")

            # 3. 自动归档至历史记录与文档链接库
            try:
                from .history_manager import save_meeting_record
                save_meeting_record(summary_data, doc_url=doc_url, scenario=cfg.default_scenario, source="card")
            except Exception as e:
                logger.warning(f"归档历史记录失败: {e}")

            self.current_summary = summary_data
            self.current_todo_index = 0
            self.state = "SUMMARIZED"
            self.render_and_send_frame()

            # 3. 广播给前端 Web 工作台及模拟器客户端
            self.broadcast({
                "event": "meeting_processed",
                "title": summary_data.get("title", "会议纪要"),
                "doc_url": summary_data.get("doc_url", ""),
                "todos": summary_data.get("todos", []),
                "decisions": summary_data.get("decisions", []),
            })

        except Exception as e:
            logger.error(f"录音自动总结流水线异常: {e}", exc_info=True)
            self.state = "IDLE"
            self.render_and_send_frame()

    def push_meeting_summary_to_card(self, summary_data: Dict[str, Any]):
        self.current_summary = summary_data
        self.current_todo_index = 0
        self.state = "SUMMARIZED"
        self.render_and_send_frame()

    def render_and_send_frame(self):
        frame_msg = self._generate_lcd_frame()
        self.broadcast(frame_msg)

    def _generate_lcd_frame(self) -> Dict[str, Any]:
        W, H = 240, 320
        img = Image.new("RGB", (W, H), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header Bar
        draw.rectangle([(0, 0), (W, 26)], fill=(30, 41, 59))
        now_str = time.strftime("%H:%M")
        draw.text((6, 5), "AI PASSPORT", fill=(250, 204, 21), font=get_font(12))
        draw.text((106, 5), now_str, fill=(241, 245, 249), font=get_font(12))
        draw.text((172, 5), f"\u7535\u91cf {self.battery_soc}%", fill=(52, 211, 153), font=get_font(12))

        font_sm = get_font(12)
        font_md = get_font(14)
        font_lg = get_font(16)

        if self.state == "IDLE":
            draw.rounded_rectangle([(65, 42), (175, 70)], radius=6, fill=(59, 130, 246))
            draw.text((82, 47), "\u3010 IDLE \u5f85\u547d \u3011", fill=(255, 255, 255), font=font_md)

            draw.text((45, 95), "\u98de\u4e66\u4f1a\u8bae\u667a\u80fd\u80f8\u5361", fill=(56, 189, 248), font=font_lg)
            
            draw.rectangle([(16, 138), (224, 252)], outline=(51, 65, 85), fill=(30, 41, 59))
            draw.text((26, 152), "\u25cf \u5355\u51fb OK: \u5f00\u59cb\u4f1a\u8bae\u5f55\u97f3", fill=(74, 222, 128), font=font_sm)
            draw.text((26, 182), "\u25cf \u518d\u6b21 OK: \u7ed3\u675f\u5e76\u63d0\u70bc\u7eaa\u8981", fill=(248, 113, 113), font=font_sm)
            draw.text((26, 212), "\u25cf UP/DOWN: \u67e5\u770b Todo \u5f85\u529e", fill=(147, 197, 253), font=font_sm)

            draw.text((40, 285), "ESP32-C3 \u00b7 16kHz \u9ad8\u6e05\u97f3\u9891", fill=(100, 116, 139), font=font_sm)

        elif self.state == "RECORDING":
            elapsed = int(time.time() - self.record_start_time)
            mm, ss = divmod(elapsed, 60)
            time_str = f"{mm:02d}:{ss:02d}"

            draw.rounded_rectangle([(50, 42), (190, 72)], radius=6, fill=(225, 29, 72))
            draw.text((64, 47), "\u25cf \u6b63\u5728\u5f55\u97f3 REC", fill=(255, 255, 255), font=font_md)

            draw.text((70, 100), time_str, fill=(255, 255, 255), font=get_font(32))

            draw.text((36, 175), "\u3010 \u9ea6\u514b\u98ce\u5168\u53cc\u5de5\u91c7\u96c6\u4e2d \u3011", fill=(251, 146, 60), font=font_md)
            draw.text((58, 205), "\u91c7\u6837\u7387: 16kHz 16-bit", fill=(148, 163, 184), font=font_sm)

            draw.rectangle([(20, 255), (220, 295)], fill=(30, 41, 59), outline=(225, 29, 72))
            draw.text((32, 267), "\u6309 [ OK ] \u7ed3\u675f\u5e76\u751f\u6210\u7eaa\u8981", fill=(253, 224, 71), font=font_sm)

        elif self.state == "PROCESSING":
            draw.rounded_rectangle([(50, 55), (190, 85)], radius=6, fill=(168, 85, 247))
            draw.text((68, 60), "\u3010 AI \u63d0\u70bc\u4e2d \u3011", fill=(255, 255, 255), font=font_md)

            draw.text((40, 135), "\u6b63\u5728\u89e3\u6790\u4f1a\u8bae\u8bed\u97f3...", fill=(241, 245, 249), font=font_md)
            draw.text((30, 170), "\u25cf \u63d0\u70bc\u6838\u5fc3\u51b3\u8bae (Decisions)", fill=(52, 211, 153), font=font_sm)
            draw.text((30, 200), "\u25cf \u68b3\u7406\u884c动\u9879 (Action Items)", fill=(96, 165, 250), font=font_sm)
            draw.text((30, 230), "\u25cf \u540c\u6b65\u98de\u4e66\u7fa4\u5361\u7247\u4e0e\u4e91\u6587\u6863", fill=(250, 204, 21), font=font_sm)

        elif self.state == "SUMMARIZED":
            draw.rounded_rectangle([(55, 34), (185, 58)], radius=4, fill=(16, 185, 129))
            draw.text((66, 38), "【 飞书文档已就绪 】", fill=(255, 255, 255), font=font_sm)

            title = self.current_summary.get("title", "会议智能纪要") if self.current_summary else "会议纪要"
            draw.text((12, 66), title[:13], fill=(56, 189, 248), font=font_md)

            draw.text((12, 92), "【 核心决议 】:", fill=(250, 204, 21), font=font_sm)
            decisions = (self.current_summary.get("decisions") or []) if self.current_summary else []
            for i, dec in enumerate(decisions[:2]):
                draw.text((16, 112 + i * 18), f"• {dec[:14]}", fill=(226, 232, 240), font=font_sm)

            todos = (self.current_summary.get("todos") or []) if self.current_summary else []
            draw.text((12, 158), f"\u3010 \u5f85\u529e \u3011({self.current_todo_index+1}/{max(1, len(todos))}):", fill=(96, 165, 250), font=font_sm)

            if todos:
                cur_todo = todos[self.current_todo_index]
                draw.rectangle([(10, 180), (230, 278)], fill=(30, 41, 59), outline=(59, 130, 246))
                
                owner = cur_todo.get("owner", "\u672a\u6307\u5b9a")
                task = cur_todo.get("task", "")
                due = cur_todo.get("due", "\u5c3d\u5feb")
                prio = cur_todo.get("priority", "P1")

                draw.text((18, 188), f"\u3010{owner}\u3011 ({prio})", fill=(251, 146, 60), font=font_md)
                draw.text((18, 214), f"\u4efb\u52a1: {task[:24]}", fill=(241, 245, 249), font=font_sm)
                draw.text((18, 248), f"\u622a\u6b62: {due}", fill=(74, 222, 128), font=font_sm)

            draw.text((20, 292), "\u6309 UP/DOWN \u7ffb\u9875 | \u6309 OK \u65b0\u5f55\u97f3", fill=(148, 163, 184), font=font_sm)

        pixels = list(img.getdata())
        rgb565 = bytearray(W * H * 2)
        idx = 0
        for r, g, b in pixels:
            px = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            rgb565[idx] = (px >> 8) & 0xFF
            rgb565[idx + 1] = px & 0xFF
            idx += 2

        b64 = base64.b64encode(rgb565).decode("ascii")
        return {
            "type": "frame",
            "x": 0,
            "y": 0,
            "w": W,
            "h": H,
            "invert": 0,
            "rgb565_b64": b64,
        }

    def broadcast(self, data: Dict[str, Any]):
        payload = json.dumps(data, separators=(",", ":")) + "\n"
        encoded = payload.encode("utf-8")
        with self._lock:
            dead_clients = []
            for client in self.clients:
                try:
                    client.sendall(encoded)
                except Exception:
                    dead_clients.append(client)
            for dc in dead_clients:
                if dc in self.clients:
                    self.clients.remove(dc)
                try:
                    dc.close()
                except Exception:
                    pass

    def send_to_client(self, client_sock: socket.socket, data: Dict[str, Any]):
        try:
            payload = json.dumps(data, separators=(",", ":")) + "\n"
            client_sock.sendall(payload.encode("utf-8"))
        except Exception as e:
            logger.debug(f"发送消息到客户端失败: {e}")

    def get_screen_state(self) -> Dict[str, Any]:
        duration = 0
        if self.state == "RECORDING" and self.record_start_time > 0:
            duration = int(time.time() - self.record_start_time)
        else:
            duration = self.record_duration

        return {
            "state": self.state,
            "is_connected": len(self.clients) > 0,
            "connected_count": len(self.clients),
            "battery_soc": self.battery_soc,
            "record_duration": duration,
            "current_summary": self.current_summary,
            "current_todo_index": self.current_todo_index,
        }

    def trigger_event(self, event_name: str) -> Dict[str, Any]:
        is_connected = len(self.clients) > 0
        if not is_connected:
            return {
                "code": -1,
                "msg": "未检测到已连接的硬件胸卡设备 (请先接入 ESP32-C3 实体胸卡至 TCP 5566 端口)",
                "data": self.get_screen_state(),
            }

        if event_name == "record_start":
            self.broadcast({"cmd": "record_start", "timestamp": time.time()})
            self._start_recording()
        elif event_name == "record_stop":
            self.broadcast({"cmd": "record_stop", "timestamp": time.time()})
            self._stop_recording()
        elif event_name == "key_prev":
            self.broadcast({"cmd": "key_press", "key": "UP", "timestamp": time.time()})
            self._handle_button_press("UP")
        elif event_name == "key_next":
            self.broadcast({"cmd": "key_press", "key": "DOWN", "timestamp": time.time()})
            self._handle_button_press("DOWN")
        elif event_name == "fetch_summary":
            self.render_and_send_frame()

        return {
            "code": 0,
            "msg": f"已向硬件胸卡下发控制指令: {event_name}",
            "data": self.get_screen_state(),
        }
