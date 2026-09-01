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


# ================= 🎴 AI Passport 核心功能模式定义 (含 NFC 门禁) =================
CARD_MODES = [
    {
        "id": "meeting",
        "name": "智能会议纪要",
        "icon": "📋",
        "desc": "高清录音/AI总结/Todo与云文档",
        "badge": "MEETING",
        "color": (59, 130, 246),
    },
    {
        "id": "nfc",
        "name": "NFC 智能门禁",
        "icon": "🔑",
        "desc": "模拟门禁/员工卡UID/刷卡开门通行",
        "badge": "NFC ACCESS",
        "color": (249, 115, 22),
    },
    {
        "id": "todo",
        "name": "每日待办打卡",
        "icon": "🎯",
        "desc": "上下键翻阅 / OK键标记完成打钩",
        "badge": "TODO",
        "color": (16, 185, 129),
    },
    {
        "id": "pomodoro",
        "name": "专注番茄时钟",
        "icon": "⏳",
        "desc": "25分钟深度专注 / 5分钟休息",
        "badge": "POMODORO",
        "color": (239, 68, 68),
    },
    {
        "id": "memo",
        "name": "语音灵感速记",
        "icon": "💡",
        "desc": "随时按住记录灵感并自动归档",
        "badge": "MEMO",
        "color": (245, 158, 11),
    },
    {
        "id": "assistant",
        "name": "AI 语音伴侣",
        "icon": "🤖",
        "desc": "随身 AI 实时问答与多机器人互动",
        "badge": "AI CHAT",
        "color": (139, 92, 246),
    },
    {
        "id": "status",
        "name": "硬件状态诊断",
        "icon": "🔋",
        "desc": "Wi-Fi信号/电量/TCP延时/内存",
        "badge": "STATUS",
        "color": (14, 165, 233),
    },
]


class CardBridge:
    def __init__(self, host: str = "0.0.0.0", port: int = 5566):
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.clients: List[socket.socket] = []
        self._lock = threading.Lock()
        self.is_running = False

        # Mode & State Machine
        # States: "MENU", "IDLE", "RECORDING", "PROCESSING", "SUMMARIZED", "NFC", "TODO", "POMODORO", "MEMO", "ASSISTANT", "STATUS"
        self.current_mode = "meeting"
        self.state = "IDLE"
        self.menu_selected_index = 0

        # Meeting data
        self.record_start_time = 0
        self.record_duration = 0
        self.battery_soc = 85
        self.current_summary: Optional[Dict[str, Any]] = None
        self.current_todo_index = 0
        self._timer_thread: Optional[threading.Thread] = None

        # ================= 🔑 NFC 智能门禁卡包数据 =================
        self.nfc_cards = [
            {"id": "card_1", "name": "公司总部主楼大门", "uid": "8A:3F:12:C9", "type": "Mifare Classic 1K", "active": True},
            {"id": "card_2", "name": "研发中心核心实验室", "uid": "E4:5B:90:A1", "type": "ISO/IEC 14443A", "active": False},
            {"id": "card_3", "name": "地下车库通行道闸", "uid": "7D:1C:44:88", "type": "Mifare Ultralight", "active": False},
            {"id": "card_4", "name": "智能工位与云打印机", "uid": "2B:A0:55:7E", "type": "NFC Type-4 Tag", "active": False},
        ]
        self.nfc_selected_index = 0
        self.nfc_swiping = False
        self.nfc_swipe_time = 0

        # Todo List Mode State
        self.daily_todos = [
            {"task": "评审《会议智能妙记》多机器人分发方案", "owner": "研发团队", "done": True, "priority": "P0"},
            {"task": "联调 AI Passport 硬件 TCP 5566 桥接", "owner": "嵌入式组", "done": False, "priority": "P0"},
            {"task": "配置飞书自建应用与群 Webhook 卡片", "owner": "管理员", "done": False, "priority": "P1"},
            {"task": "同步钉钉 Stream 与 QQ OneBot 反向连接", "owner": "运维团队", "done": False, "priority": "P1"},
        ]
        self.todo_selected_index = 0

        # Pomodoro Mode State
        self.pomo_duration_total = 25 * 60
        self.pomo_remaining_seconds = 25 * 60
        self.pomo_is_running = False
        self.pomo_mode_type = "WORK" # "WORK" or "BREAK"

        # Voice Memo State
        self.memos_count = 3

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
                if state == "down" or state == "click":
                    self._handle_button_press(name)

            elif msg_type == "battery":
                self.battery_soc = msg.get("soc", 85)
                self.render_and_send_frame()

            elif msg_type in ["combo_menu", "back_to_menu"]:
                self.enter_menu()

            elif msg_type == "select_mode":
                mode_id = msg.get("mode_id", "meeting")
                self.enter_mode(mode_id)

            elif msg_type in ["nfc_swipe", "swipe"]:
                self.trigger_nfc_swipe()

            elif msg_type == "record_start":
                self._start_recording()
            elif msg_type == "record_stop":
                self._stop_recording()
            elif msg_type == "fetch_summary":
                self.render_and_send_frame()
            elif msg_type == "ping":
                self.send_to_client(client_sock, {"event": "pong", "time": time.time()})

        except Exception as e:
            logger.error(f"处理卡片消息失败: {e}, 原始消息: {message_str}")

    # ================= 🎛️ 菜单与多模式切换状态机 =================

    def enter_menu(self):
        """返回功能选择菜单 (可上下选择)"""
        logger.info("🎛️ AI Passport 触发组合键，已返回【功能选择菜单】")
        self.state = "MENU"
        self.render_and_send_frame()
        self.broadcast({
            "event": "state_changed",
            "state": "MENU",
            "mode": "menu",
            "selected_index": self.menu_selected_index,
            "modes": CARD_MODES,
        })

    def enter_mode(self, mode_id: str):
        """进入选中的功能模式"""
        self.current_mode = mode_id
        for idx, m in enumerate(CARD_MODES):
            if m["id"] == mode_id:
                self.menu_selected_index = idx
                break

        if mode_id == "meeting":
            self.state = "SUMMARIZED" if self.current_summary else "IDLE"
        elif mode_id == "nfc":
            self.state = "NFC"
        elif mode_id == "todo":
            self.state = "TODO"
        elif mode_id == "pomodoro":
            self.state = "POMODORO"
        elif mode_id == "memo":
            self.state = "MEMO"
        elif mode_id == "assistant":
            self.state = "ASSISTANT"
        elif mode_id == "status":
            self.state = "STATUS"
        else:
            self.state = "IDLE"

        logger.info(f"✨ AI Passport 已进入功能模式: {mode_id} (State: {self.state})")
        self.render_and_send_frame()
        self.broadcast({
            "event": "mode_entered",
            "mode_id": mode_id,
            "state": self.state,
        })

    def trigger_nfc_swipe(self):
        """触发 NFC 门禁模拟刷卡通行"""
        cur_card = self.nfc_cards[self.nfc_selected_index % len(self.nfc_cards)]
        logger.info(f"🔑 AI Passport 触发 NFC 刷卡通行: {cur_card['name']} (UID: {cur_card['uid']})")
        self.nfc_swiping = True
        self.nfc_swipe_time = time.time()
        self.render_and_send_frame()

        self.broadcast({
            "event": "nfc_swiped",
            "card": cur_card,
            "timestamp": time.time()
        })

        def _reset_swipe():
            time.sleep(2.0)
            self.nfc_swiping = False
            self.render_and_send_frame()

        threading.Thread(target=_reset_swipe, daemon=True).start()

    def add_nfc_card(self, name: str, uid: str, card_type: str = "Mifare Classic 1K") -> Dict[str, Any]:
        new_card = {
            "id": f"card_{int(time.time()*1000)}",
            "name": name.strip() or "未命名门禁卡",
            "uid": uid.strip().upper(),
            "type": card_type,
            "active": False,
        }
        self.nfc_cards.append(new_card)
        self.render_and_send_frame()
        return new_card

    def delete_nfc_card(self, card_id: str) -> bool:
        initial_len = len(self.nfc_cards)
        self.nfc_cards = [c for c in self.nfc_cards if c["id"] != card_id]
        if self.nfc_selected_index >= len(self.nfc_cards):
            self.nfc_selected_index = max(0, len(self.nfc_cards) - 1)
        self.render_and_send_frame()
        return len(self.nfc_cards) < initial_len

    def _handle_button_press(self, btn_name: str):
        logger.info(f"收到硬件按键触发: {btn_name}, 当前状态: {self.state}, 当前模式: {self.current_mode}")

        # 1. 处于功能选择菜单中 (MENU 状态)
        if self.state == "MENU":
            if btn_name in ["UP", "KEY_UP", "key_up"]:
                self.menu_selected_index = (self.menu_selected_index - 1) % len(CARD_MODES)
                self.render_and_send_frame()
            elif btn_name in ["DOWN", "KEY_DOWN", "key_down"]:
                self.menu_selected_index = (self.menu_selected_index + 1) % len(CARD_MODES)
                self.render_and_send_frame()
            elif btn_name in ["OK", "KEY_OK", "key_ok"]:
                selected_mode = CARD_MODES[self.menu_selected_index]["id"]
                self.enter_mode(selected_mode)
            return

        # 2. 处于各具体功能模式内
        if btn_name in ["COMBO_MENU", "combo_menu", "BACK_TO_MENU", "back_to_menu"]:
            self.enter_menu()
            return

        # 模式 1: NFC 智能门禁模式 (NFC)
        if self.current_mode == "nfc" or self.state == "NFC":
            if btn_name in ["UP", "KEY_UP", "key_up"]:
                if self.nfc_cards:
                    self.nfc_selected_index = (self.nfc_selected_index - 1) % len(self.nfc_cards)
                    self.render_and_send_frame()
            elif btn_name in ["DOWN", "KEY_DOWN", "key_down"]:
                if self.nfc_cards:
                    self.nfc_selected_index = (self.nfc_selected_index + 1) % len(self.nfc_cards)
                    self.render_and_send_frame()
            elif btn_name in ["OK", "KEY_OK", "key_ok"]:
                self.trigger_nfc_swipe()
            return

        # 模式 2: 智能会议模式 (Meeting)
        if self.current_mode == "meeting" or self.state in ["IDLE", "RECORDING", "PROCESSING", "SUMMARIZED"]:
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

        # 模式 3: 每日待办打卡 (Todo)
        elif self.current_mode == "todo" or self.state == "TODO":
            if btn_name == "UP":
                self.todo_selected_index = max(0, self.todo_selected_index - 1)
                self.render_and_send_frame()
            elif btn_name == "DOWN":
                self.todo_selected_index = min(len(self.daily_todos) - 1, self.todo_selected_index + 1)
                self.render_and_send_frame()
            elif btn_name == "OK":
                if 0 <= self.todo_selected_index < len(self.daily_todos):
                    self.daily_todos[self.todo_selected_index]["done"] = not self.daily_todos[self.todo_selected_index]["done"]
                    self.render_and_send_frame()

        # 模式 4: 专注番茄钟 (Pomodoro)
        elif self.current_mode == "pomodoro" or self.state == "POMODORO":
            if btn_name == "OK":
                self.pomo_is_running = not self.pomo_is_running
                if self.pomo_is_running:
                    threading.Thread(target=self._pomo_timer_loop, daemon=True).start()
                self.render_and_send_frame()
            elif btn_name == "DOWN": # 重置
                self.pomo_is_running = False
                self.pomo_remaining_seconds = self.pomo_duration_total
                self.render_and_send_frame()
            elif btn_name == "UP": # 切换专注/休息
                self.pomo_mode_type = "BREAK" if self.pomo_mode_type == "WORK" else "WORK"
                self.pomo_duration_total = (5 * 60) if self.pomo_mode_type == "BREAK" else (25 * 60)
                self.pomo_remaining_seconds = self.pomo_duration_total
                self.render_and_send_frame()

        # 模式 5: 语音灵感速记 (Memo)
        elif self.current_mode == "memo" or self.state == "MEMO":
            if btn_name == "OK":
                self.memos_count += 1
                self.render_and_send_frame()

        # 模式 6: 状态诊断 (Status)
        elif self.current_mode == "status" or self.state == "STATUS":
            if btn_name in ["UP", "DOWN", "OK"]:
                self.render_and_send_frame()

    def _pomo_timer_loop(self):
        while self.is_running and self.pomo_is_running and self.state == "POMODORO":
            time.sleep(1.0)
            if self.pomo_is_running and self.state == "POMODORO":
                if self.pomo_remaining_seconds > 0:
                    self.pomo_remaining_seconds -= 1
                    self.render_and_send_frame()
                else:
                    self.pomo_is_running = False
                    self.render_and_send_frame()
                    break

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
            doc_url = None
            try:
                doc_url = feishu.create_feishu_doc(summary_data)
                summary_data["doc_url"] = doc_url
                logger.info(f"✔ 卡片录音结束，已自动生成飞书云文档: {doc_url}")
            except Exception as e:
                logger.warning(f"自动创建飞书文档异常: {e}")
                summary_data["doc_url"] = "https://feishu.cn"

            # 2. 自动向启用的多机器人渠道广播通知
            try:
                from .bot_notifier import BotNotifier
                bot_res = BotNotifier.broadcast(cfg.bot_channels, summary_data, doc_url=doc_url or "")
                logger.info(f"✔ 卡片录音结束，已自动向启用的多机器人渠道广播通知: {bot_res}")
            except Exception as e:
                logger.warning(f"自动广播多机器人通知失败: {e}")

            # 3. 自动归档至历史记录
            try:
                from .history_manager import save_meeting_record
                save_meeting_record(summary_data, doc_url=doc_url, scenario=cfg.default_scenario, source="card")
            except Exception as e:
                logger.warning(f"归档历史记录失败: {e}")

            self.current_summary = summary_data
            self.current_todo_index = 0
            self.state = "SUMMARIZED"
            self.render_and_send_frame()

            # 4. 广播给前端 Web 工作台及模拟器客户端
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
        self.current_mode = "meeting"
        self.state = "SUMMARIZED"
        self.render_and_send_frame()

    def render_and_send_frame(self):
        frame_msg = self._generate_lcd_frame()
        self.broadcast(frame_msg)

    # ================= 🎨 240x320 高清 LCD 画面渲染引擎 =================

    def _generate_lcd_frame(self) -> Dict[str, Any]:
        W, H = 240, 320
        img = Image.new("RGB", (W, H), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Header Bar
        draw.rectangle([(0, 0), (W, 26)], fill=(30, 41, 59))
        now_str = time.strftime("%H:%M")
        draw.text((6, 5), "AI PASSPORT", fill=(250, 204, 21), font=get_font(12))
        draw.text((106, 5), now_str, fill=(241, 245, 249), font=get_font(12))
        draw.text((172, 5), f"电量 {self.battery_soc}%", fill=(52, 211, 153), font=get_font(12))

        font_sm = get_font(12)
        font_md = get_font(14)
        font_lg = get_font(16)

        # ================= 🎛️ 1. 功能选择主菜单 (MENU 状态) =================
        if self.state == "MENU":
            draw.rounded_rectangle([(40, 32), (200, 56)], radius=6, fill=(59, 130, 246))
            draw.text((58, 36), "【 功能选择菜单 】", fill=(255, 255, 255), font=font_md)

            # 菜单选项卡片列表 (上下按键翻阅，当前项高亮)
            start_y = 62
            for idx, mode in enumerate(CARD_MODES):
                y = start_y + idx * 32
                is_selected = (idx == self.menu_selected_index)

                if is_selected:
                    draw.rounded_rectangle([(10, y), (230, y + 28)], radius=5, fill=(30, 58, 138), outline=(56, 189, 248), width=2)
                    draw.text((16, y + 5), f"▶ {mode['icon']} {mode['name']}", fill=(255, 255, 255), font=font_md)
                    draw.text((165, y + 7), mode['badge'], fill=(125, 211, 252), font=get_font(9))
                else:
                    draw.rounded_rectangle([(10, y), (230, y + 28)], radius=5, fill=(30, 41, 59), outline=(51, 65, 85))
                    draw.text((18, y + 5), f"{mode['icon']} {mode['name']}", fill=(203, 213, 225), font=font_md)
                    draw.text((165, y + 7), mode['badge'], fill=(100, 116, 139), font=get_font(9))

            # 底部按键提示
            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((18, 296), "▲/▼:选择  OK:进入  UP+DN:菜单", fill=(148, 163, 184), font=font_sm)

        # ================= 🔑 2. NFC 智能门禁模式 (NFC 状态) =================
        elif self.state == "NFC":
            draw.rounded_rectangle([(42, 32), (198, 56)], radius=6, fill=(249, 115, 22))
            draw.text((58, 36), "🔑 NFC 智能门禁通行", fill=(255, 255, 255), font=font_md)

            if self.nfc_cards:
                cur_card = self.nfc_cards[self.nfc_selected_index % len(self.nfc_cards)]
                
                # 拟真门禁卡片芯片面板
                card_bg = (67, 56, 202) if not self.nfc_swiping else (16, 185, 129)
                border_color = (129, 140, 248) if not self.nfc_swiping else (74, 222, 128)
                draw.rounded_rectangle([(12, 64), (228, 215)], radius=12, fill=card_bg, outline=border_color, width=2)

                # 芯片线圈与标题
                draw.text((22, 74), "((( NFC ACCESS PASS )))", fill=(253, 224, 71), font=get_font(11))
                draw.text((22, 94), cur_card.get("name", "智能门禁卡")[:13], fill=(255, 255, 255), font=font_md)
                draw.text((22, 120), f"协议: {cur_card.get('type', 'ISO14443-A')}", fill=(226, 232, 240), font=font_sm)

                # UID 芯片编号高亮展示
                draw.rectangle([(20, 145), (220, 180)], fill=(15, 23, 42), outline=(250, 204, 21))
                draw.text((26, 150), "卡片 UID (Chip ID):", fill=(148, 163, 184), font=get_font(10))
                draw.text((26, 162), cur_card.get("uid", "8A:3F:12:C9"), fill=(56, 189, 248), font=font_md)

                draw.text((22, 190), f"卡包序号: [{self.nfc_selected_index + 1}/{len(self.nfc_cards)}]", fill=(203, 213, 225), font=get_font(11))

                # 刷卡动画或就绪提示
                if self.nfc_swiping:
                    draw.rectangle([(16, 224), (224, 275)], fill=(16, 185, 129), outline=(74, 222, 128))
                    draw.text((32, 234), "🟢 刷卡成功！门禁已解锁", fill=(255, 255, 255), font=font_md)
                    draw.text((45, 256), "射频发射中 · 欢迎通行", fill=(241, 245, 249), font=get_font(11))
                else:
                    draw.rectangle([(16, 224), (224, 275)], fill=(30, 41, 59), outline=(51, 65, 85))
                    draw.text((32, 234), "📡 贴近读卡器感应区", fill=(250, 204, 21), font=font_md)
                    draw.text((38, 256), "单击 [ OK ] 立即模拟刷卡", fill=(148, 163, 184), font=font_sm)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((12, 296), "▲/▼:换卡  OK:刷卡开门  UP+DN:菜单", fill=(148, 163, 184), font=font_sm)

        # ================= 📋 3. 会议待命 (IDLE 状态) =================
        elif self.state == "IDLE":
            draw.rounded_rectangle([(65, 38), (175, 66)], radius=6, fill=(59, 130, 246))
            draw.text((82, 43), "【 会议待命 】", fill=(255, 255, 255), font=font_md)

            draw.text((45, 85), "会议智能录音与纪要", fill=(56, 189, 248), font=font_lg)

            draw.rectangle([(16, 120), (224, 250)], outline=(51, 65, 85), fill=(30, 41, 59))
            draw.text((26, 132), "● 单击 OK: 开始会议录音", fill=(74, 222, 128), font=font_sm)
            draw.text((26, 162), "● 再次 OK: 结束并提炼纪要", fill=(248, 113, 113), font=font_sm)
            draw.text((26, 192), "● UP/DN: 翻阅 Todo 任务", fill=(147, 197, 253), font=font_sm)
            draw.text((26, 222), "● 组合键: 返回功能选择菜单", fill=(250, 204, 21), font=font_sm)

            draw.text((25, 265), "ESP32-C3 · 16kHz 高清音频", fill=(100, 116, 139), font=font_sm)
            draw.rectangle([(0, 290), (W, 320)], fill=(2, 6, 23))
            draw.text((32, 298), "OK: 录音  |  UP+DN: 选功能", fill=(148, 163, 184), font=font_sm)

        # ================= 🎙️ 4. 正在录音 (RECORDING 状态) =================
        elif self.state == "RECORDING":
            elapsed = int(time.time() - self.record_start_time)
            mm, ss = divmod(elapsed, 60)
            time_str = f"{mm:02d}:{ss:02d}"

            draw.rounded_rectangle([(50, 42), (190, 72)], radius=6, fill=(225, 29, 72))
            draw.text((64, 47), "● 正在录音 REC", fill=(255, 255, 255), font=font_md)

            draw.text((70, 95), time_str, fill=(255, 255, 255), font=get_font(32))

            draw.text((36, 165), "【 麦克风全双工采集 】", fill=(251, 146, 60), font=font_md)
            draw.text((58, 195), "采样率: 16kHz 16-bit", fill=(148, 163, 184), font=font_sm)

            draw.rectangle([(20, 240), (220, 275)], fill=(30, 41, 59), outline=(225, 29, 72))
            draw.text((32, 250), "按 [ OK ] 结束并生成纪要", fill=(253, 224, 71), font=font_sm)

            draw.rectangle([(0, 290), (W, 320)], fill=(2, 6, 23))
            draw.text((25, 298), "OK: 结束录音 | UP+DN: 取消返回", fill=(148, 163, 184), font=font_sm)

        # ================= 🧠 5. AI 提炼中 (PROCESSING 状态) =================
        elif self.state == "PROCESSING":
            draw.rounded_rectangle([(50, 50), (190, 80)], radius=6, fill=(168, 85, 247))
            draw.text((68, 55), "【 AI 提炼中 】", fill=(255, 255, 255), font=font_md)

            draw.text((40, 115), "正在解析会议语音...", fill=(241, 245, 249), font=font_md)
            draw.text((30, 150), "● 提炼核心决议 (Decisions)", fill=(52, 211, 153), font=font_sm)
            draw.text((30, 180), "● 梳理行动项 (Action Items)", fill=(96, 165, 250), font=font_sm)
            draw.text((30, 210), "● 广播 12 大多机器人推送渠道", fill=(250, 204, 21), font=font_sm)
            draw.text((30, 240), "● 同步创建飞书 Docx 云文档", fill=(56, 189, 248), font=font_sm)

        # ================= 📄 6. 纪要展示 (SUMMARIZED 状态) =================
        elif self.state == "SUMMARIZED":
            draw.rounded_rectangle([(45, 32), (195, 56)], radius=4, fill=(16, 185, 129))
            draw.text((68, 36), "【 纪要已生成 】", fill=(255, 255, 255), font=font_md)

            title = (self.current_summary.get("title", "会议纪要")) if self.current_summary else "会议纪要"
            draw.text((12, 65), title[:14], fill=(255, 255, 255), font=font_md)

            todos = self.current_summary.get("todos", []) if self.current_summary else []
            if todos:
                cur_todo = todos[self.current_todo_index % len(todos)]
                draw.rectangle([(8, 92), (232, 230)], fill=(30, 41, 59), outline=(56, 189, 248), width=1)

                draw.text((16, 100), f"Todo 待办 ({self.current_todo_index + 1}/{len(todos)})", fill=(56, 189, 248), font=font_md)
                draw.text((16, 126), f"责任人: {cur_todo.get('owner', '待定')}", fill=(250, 204, 21), font=font_sm)
                draw.text((16, 148), f"截止: {cur_todo.get('due', '尽快')}", fill=(148, 163, 184), font=font_sm)

                task_text = cur_todo.get("task", "")
                draw.text((16, 175), task_text[:15], fill=(241, 245, 249), font=font_sm)
                if len(task_text) > 15:
                    draw.text((16, 195), task_text[15:30], fill=(241, 245, 249), font=font_sm)
            else:
                draw.rectangle([(8, 92), (232, 230)], fill=(30, 41, 59), outline=(71, 85, 105))
                draw.text((45, 140), "已完成会议总结分析", fill=(241, 245, 249), font=font_md)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((16, 296), "▲/▼:切Todo  OK:重录  UP+DN:菜单", fill=(148, 163, 184), font=font_sm)

        # ================= 🎯 7. 每日待办打卡模式 (TODO 状态) =================
        elif self.state == "TODO":
            draw.rounded_rectangle([(45, 34), (195, 58)], radius=6, fill=(16, 185, 129))
            draw.text((62, 38), "🎯 每日待办打卡", fill=(255, 255, 255), font=font_md)

            done_cnt = sum(1 for t in self.daily_todos if t.get("done"))
            draw.text((14, 66), f"任务完成度: {done_cnt}/{len(self.daily_todos)}", fill=(52, 211, 153), font=font_sm)

            start_y = 90
            for idx, item in enumerate(self.daily_todos):
                y = start_y + idx * 46
                is_sel = (idx == self.todo_selected_index)
                is_done = item.get("done", False)

                bg_color = (30, 58, 138) if is_sel else (30, 41, 59)
                border_color = (56, 189, 248) if is_sel else (51, 65, 85)
                draw.rounded_rectangle([(10, y), (230, y + 40)], radius=6, fill=bg_color, outline=border_color)

                mark = "☑ [已完成]" if is_done else "☐ [进行中]"
                mark_color = (74, 222, 128) if is_done else (251, 146, 60)
                draw.text((16, y + 5), mark, fill=mark_color, font=font_sm)
                draw.text((105, y + 5), f"[{item.get('owner', '')}]", fill=(148, 163, 184), font=get_font(11))
                draw.text((16, y + 22), item.get("task", "")[:15], fill=(255, 255, 255), font=font_sm)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((12, 296), "▲/▼:选择  OK:打钩/取消  UP+DN:菜单", fill=(148, 163, 184), font=font_sm)

        # ================= ⏳ 8. 专注番茄时钟 (POMODORO 状态) =================
        elif self.state == "POMODORO":
            pomo_color = (239, 68, 68) if self.pomo_mode_type == "WORK" else (16, 185, 129)
            pomo_title = "⏳ 深度专注工作" if self.pomo_mode_type == "WORK" else "☕ 休息时间"
            draw.rounded_rectangle([(45, 36), (195, 62)], radius=6, fill=pomo_color)
            draw.text((62, 41), pomo_title, fill=(255, 255, 255), font=font_md)

            mm, ss = divmod(self.pomo_remaining_seconds, 60)
            time_display = f"{mm:02d}:{ss:02d}"
            draw.text((55, 100), time_display, fill=(255, 255, 255), font=get_font(42))

            status_txt = "🔥 正在专注倒计时..." if self.pomo_is_running else "⏸️ 已暂停 (按 OK 开始)"
            draw.text((50, 180), status_txt, fill=(253, 224, 71), font=font_md)

            # 进度条
            progress = 1.0 - (self.pomo_remaining_seconds / max(1, self.pomo_duration_total))
            draw.rectangle([(20, 220), (220, 235)], fill=(30, 41, 59), outline=(71, 85, 105))
            fill_w = int(20 + progress * 200)
            draw.rectangle([(20, 220), (fill_w, 235)], fill=pomo_color)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((8, 296), "OK:启停  ▲:切工/休  ▼:重置  UP+DN:菜单", fill=(148, 163, 184), font=font_sm)

        # ================= 💡 9. 语音灵感速记 (MEMO 状态) =================
        elif self.state == "MEMO":
            draw.rounded_rectangle([(45, 36), (195, 62)], radius=6, fill=(245, 158, 11))
            draw.text((62, 41), "💡 语音灵感速记", fill=(255, 255, 255), font=font_md)

            draw.text((30, 105), f"已归档灵感条目: {self.memos_count} 条", fill=(250, 204, 21), font=font_md)

            draw.rectangle([(16, 145), (224, 245)], fill=(30, 41, 59), outline=(245, 158, 11))
            draw.text((26, 160), "● 按 OK 记录 15秒灵感语音", fill=(74, 222, 128), font=font_sm)
            draw.text((26, 190), "● AI 自动转文字并保存便签", fill=(147, 197, 253), font=font_sm)
            draw.text((26, 220), "● 自动同步推送至手机/群聊", fill=(248, 113, 113), font=font_sm)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((25, 296), "OK: 录制速记  |  UP+DN: 选功能", fill=(148, 163, 184), font=font_sm)

        # ================= 🔋 10. 硬件状态诊断 (STATUS 状态) =================
        elif self.state == "STATUS":
            draw.rounded_rectangle([(45, 36), (195, 62)], radius=6, fill=(14, 165, 233))
            draw.text((62, 41), "🔋 硬件状态诊断", fill=(255, 255, 255), font=font_md)

            draw.rectangle([(12, 85), (228, 260)], fill=(30, 41, 59), outline=(51, 65, 85))
            draw.text((20, 98), "● 主控: ESP32-C3 (8MB Flash)", fill=(241, 245, 249), font=font_sm)
            draw.text((20, 128), f"● 电池电量: {self.battery_soc}% (正常)", fill=(52, 211, 153), font=font_sm)
            draw.text((20, 158), "● Wi-Fi 状态: 已连入局域网", fill=(56, 189, 248), font=font_sm)
            draw.text((20, 188), f"● TCP 桥接端口: 5566 (在线)", fill=(250, 204, 21), font=font_sm)
            draw.text((20, 218), f"● 在线客户端数: {len(self.clients)} 台", fill=(168, 85, 247), font=font_sm)

            draw.rectangle([(0, 288), (W, 320)], fill=(2, 6, 23))
            draw.text((35, 296), "按 组合键 (UP+DN) 返回菜单", fill=(148, 163, 184), font=font_sm)

        # 转换为 base64 PNG 帧供 Web 实时显示
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_png = base64.b64encode(buf.getvalue()).decode("utf-8")

        return {
            "type": "lcd_frame",
            "state": self.state,
            "mode": self.current_mode,
            "menu_selected_index": self.menu_selected_index,
            "battery_soc": self.battery_soc,
            "nfc_cards": self.nfc_cards,
            "nfc_selected_index": self.nfc_selected_index,
            "nfc_swiping": self.nfc_swiping,
            "frame_b64": b64_png,
            "timestamp": time.time(),
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

        frame = self._generate_lcd_frame()

        return {
            "state": self.state,
            "mode": self.current_mode,
            "modes_list": CARD_MODES,
            "menu_selected_index": self.menu_selected_index,
            "nfc_cards": self.nfc_cards,
            "nfc_selected_index": self.nfc_selected_index,
            "nfc_swiping": self.nfc_swiping,
            "is_connected": len(self.clients) > 0,
            "connected_count": len(self.clients),
            "battery_soc": self.battery_soc,
            "record_duration": duration,
            "current_summary": self.current_summary,
            "current_todo_index": self.current_todo_index,
            "frame_b64": frame.get("frame_b64", ""),
        }

    def trigger_event(self, event_name: str) -> Dict[str, Any]:
        logger.info(f"触发卡片控制指令: {event_name}")

        # 组合键返回菜单
        if event_name in ["combo_menu", "back_to_menu", "menu"]:
            self.enter_menu()
            self.broadcast({"cmd": "combo_menu", "timestamp": time.time()})
            return {"code": 0, "msg": "已触发组合键，返回功能选择菜单", "data": self.get_screen_state()}

        # NFC 刷卡开门
        elif event_name in ["nfc_swipe", "swipe"]:
            self.trigger_nfc_swipe()
            return {"code": 0, "msg": "已触发 NFC 模拟刷卡通行", "data": self.get_screen_state()}

        # 向上 / 向下 / 确认
        elif event_name in ["key_up", "key_prev"]:
            self._handle_button_press("UP")
            self.broadcast({"cmd": "key_press", "key": "UP", "timestamp": time.time()})
        elif event_name in ["key_down", "key_next"]:
            self._handle_button_press("DOWN")
            self.broadcast({"cmd": "key_press", "key": "DOWN", "timestamp": time.time()})
        elif event_name in ["key_ok", "ok"]:
            self._handle_button_press("OK")
            self.broadcast({"cmd": "key_press", "key": "OK", "timestamp": time.time()})

        # 会议录音启停
        elif event_name == "record_start":
            self.broadcast({"cmd": "record_start", "timestamp": time.time()})
            self._start_recording()
        elif event_name == "record_stop":
            self.broadcast({"cmd": "record_stop", "timestamp": time.time()})
            self._stop_recording()
        elif event_name == "fetch_summary":
            self.render_and_send_frame()

        return {
            "code": 0,
            "msg": f"已执行指令: {event_name}",
            "data": self.get_screen_state(),
        }


_global_card_bridge: Optional[CardBridge] = None


def get_card_bridge(host: str = "0.0.0.0", port: int = 5566) -> CardBridge:
    global _global_card_bridge
    if _global_card_bridge is None:
        _global_card_bridge = CardBridge(host=host, port=port)
        _global_card_bridge.start()
    return _global_card_bridge
