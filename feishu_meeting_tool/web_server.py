# -*- coding: utf-8 -*-
import os
import json
import time
import shutil
import logging
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .config import get_config, save_config, AppConfig, DEFAULT_LLM_PROVIDERS
from .feishu_client import FeishuClient
from .ai_summarizer import AISummarizer
from .audio_pipeline import AudioPipeline
from .card_bridge import CardBridge

logger = logging.getLogger("web_server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="会议智能妙记", version="1.0.0")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

bridge: Optional[CardBridge] = None


def get_auth_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("session_token")
    auth_header = request.headers.get("authorization", "")
    if not token and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if token:
        return validate_session(token)
    return None


def get_feishu_client(user_id: Optional[str] = None) -> FeishuClient:
    cfg = get_config(user_id)
    return FeishuClient(
        app_id=cfg.feishu_app_id,
        app_secret=cfg.feishu_app_secret,
        webhook_url=cfg.feishu_bot_webhook_url,
        bot_secret=cfg.feishu_bot_secret,
        doc_folder_token=cfg.feishu_doc_folder_token,
        bitable_app_token=cfg.feishu_bitable_app_token,
        bitable_table_id=cfg.feishu_bitable_table_id,
    )


def get_ai_summarizer(user_id: Optional[str] = None) -> AISummarizer:
    cfg = get_config(user_id)
    return AISummarizer(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        model=cfg.llm_model,
        temperature=cfg.llm_temperature,
    )


def get_audio_pipeline(user_id: Optional[str] = None) -> AudioPipeline:
    cfg = get_config(user_id)
    return AudioPipeline(
        records_dir=cfg.records_dir,
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
    )


def get_card_bridge() -> CardBridge:
    global bridge
    if bridge is None:
        cfg = get_config()
        bridge = CardBridge(host=cfg.bridge_host, port=cfg.bridge_port)
        bridge.start()
    return bridge


@app.on_event("startup")
def startup_event():
    # 1. 自动初始化 PostgreSQL 企业级 13 大数据表与种子数据
    try:
        from .db_engine import get_pg_engine
        get_pg_engine()
    except Exception as e:
        logger.warning(f"启动初始化 PostgreSQL 数据库异常: {e}")

    # 2. 启动 AI Passport 硬件通信桥接
    get_card_bridge()
    logger.info("会议智能妙记 Web 服务、PostgreSQL 企业数据库与硬件桥接已启动")


from .auth_manager import (
    register_user,
    authenticate_user,
    create_session,
    validate_session,
    delete_session,
)


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
        media_type="text/html; charset=utf-8",
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={},
        media_type="text/html; charset=utf-8",
    )


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={},
        media_type="text/html; charset=utf-8",
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    email: Optional[str] = None


@app.post("/api/auth/login")
async def api_login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        return {"code": -1, "msg": "用户名或密码错误，请核对后重试"}
    token = create_session(user["id"])
    return {
        "code": 0,
        "msg": "登录成功",
        "token": token,
        "user": user,
    }


@app.post("/api/auth/register")
async def api_register(req: RegisterRequest):
    try:
        res = register_user(
            username=req.username,
            password=req.password,
            display_name=req.display_name,
            email=req.email,
        )
        if res.get("code") != 0:
            return res

        user = res.get("user", {})
        token = create_session(user["id"])
        return {
            "code": 0,
            "msg": "注册成功",
            "token": token,
            "user": user,
        }
    except Exception as e:
        return {"code": -1, "msg": str(e)}


@app.get("/api/auth/me")
async def api_get_current_user(request: Request):
    token = request.cookies.get("session_token")
    auth_header = request.headers.get("authorization", "")
    if not token and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()

    if not token:
        return {"code": 401, "msg": "未登录", "user": None}

    user = validate_session(token)
    if not user:
        return {"code": 401, "msg": "登录已过期或无效", "user": None}

    return {
        "code": 0,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name", user["username"]),
            "role": user.get("role", "user"),
            "email": user.get("email", ""),
        },
    }


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        delete_session(token)
    return {"code": 0, "msg": "已成功退出登录"}


@app.get("/api/config")
async def get_configuration(request: Request):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    from dataclasses import asdict
    return asdict(get_config(user_id))


@app.post("/api/config")
async def update_configuration(request: Request, config_data: dict):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    save_config(config_data, user_id=user_id)
    return {"code": 0, "msg": "个人专属配置保存成功！"}


@app.get("/api/ai/presets")
async def get_ai_presets():
    return {"code": 0, "data": DEFAULT_LLM_PROVIDERS}


class AITestRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"


class FetchModelsRequest(BaseModel):
    api_key: str
    base_url: str = "https://api.openai.com/v1"


@app.post("/api/ai/test")
async def test_ai_connection(request: Request):
    data = await request.json()
    p = data.get("provider", data)
    api_key = p.get("api_key", "")
    base_url = p.get("base_url", "https://api.openai.com/v1")
    model = p.get("model", "gpt-4o-mini")

    res = AISummarizer.test_connection(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    return res


class PGTestRequest(BaseModel):
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_database: str = "feishu_meeting"
    pg_user: str = "postgres"
    pg_password: str = ""
    database_url: Optional[str] = None


@app.post("/api/db/test")
async def test_database_connection(request: Request):
    from .db_engine import test_pg_connection, reset_pg_engine
    data = await request.json()
    # Normalize keys (pg_host vs host)
    norm = {
        "pg_host": data.get("pg_host", data.get("host", "127.0.0.1")),
        "pg_port": int(data.get("pg_port", data.get("port", 5432))),
        "pg_database": data.get("pg_database", data.get("database", "feishu_meeting")),
        "pg_user": data.get("pg_user", data.get("user", "postgres")),
        "pg_password": data.get("pg_password", data.get("password", "")),
        "database_url": data.get("database_url")
    }
    res = test_pg_connection(norm)
    if res.get("code") == 0:
        reset_pg_engine()
    return res


@app.post("/api/ai/fetch_models")
async def fetch_gateway_models(req: FetchModelsRequest):
    res = AISummarizer.fetch_models_from_gateway(
        api_key=req.api_key,
        base_url=req.base_url,
    )
    return res


@app.post("/api/summarize")
async def summarize_meeting(
    request: Request,
    scenario: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    auto_create_doc: Optional[str] = Form(None),
    auto_push_bot: Optional[str] = Form(None),
):
    user = get_auth_user(request)
    user_id = user["id"] if user else "u_admin"
    username = user.get("display_name") or user.get("username", "admin") if user else "管理员"

    content_type = request.headers.get("content-type", "")
    req_scenario = scenario or "general"
    req_text = text or ""
    req_auto_doc = auto_create_doc or "false"
    req_auto_bot = auto_push_bot or "false"

    if "application/json" in content_type:
        try:
            body = await request.json()
            req_scenario = body.get("scenario", req_scenario)
            req_text = body.get("text", req_text)
            req_auto_doc = str(body.get("auto_create_doc", req_auto_doc))
            req_auto_bot = str(body.get("auto_push_bot", req_auto_bot))
        except Exception:
            pass

    transcript = req_text
    pipeline = get_audio_pipeline(user_id)

    if audio_file:
        records_dir = Path("records")
        records_dir.mkdir(exist_ok=True)
        file_path = records_dir / audio_file.filename
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(audio_file.file, buffer)
        
        try:
            transcript = pipeline.transcribe(str(file_path))
        except Exception as e:
            return {"code": -1, "msg": f"语音识别 (ASR) 失败: {str(e)}"}

    if not transcript or not transcript.strip():
        return {"code": -1, "msg": "请输入或上传真实的会议内容，无法对空内容进行总结"}

    summarizer = get_ai_summarizer(user_id)
    try:
        summary_data = summarizer.summarize(transcript, scenario=req_scenario)
    except Exception as e:
        return {"code": -1, "msg": f"AI 大模型提炼失败: {str(e)}"}

    doc_url = None
    bot_res = None
    feishu = get_feishu_client(user_id)

    is_create_doc = str(req_auto_doc).lower() in ["true", "1", "yes"]
    if is_create_doc:
        try:
            doc_url = feishu.create_feishu_doc(summary_data)
            summary_data["doc_url"] = doc_url
        except Exception as e:
            logger.warning(f"自动创建云文档异常: {e}")

    is_push_bot = str(req_auto_bot).lower() in ["true", "1", "yes"]
    if is_push_bot:
        try:
            from .bot_notifier import BotNotifier
            bot_res = BotNotifier.broadcast(cfg.bot_channels, summary_data, doc_url=doc_url or "")
        except Exception as e:
            logger.warning(f"多机器人互推广播异常: {e}")

    try:
        get_card_bridge().push_meeting_summary_to_card(summary_data)
    except Exception as e:
        logger.warning(f"同步至卡片失败: {e}")

    # 自动归档至用户专属历史记录
    from .history_manager import save_meeting_record, update_record_doc_url
    rec = save_meeting_record(
        summary_data,
        doc_url=doc_url or "",
        scenario=req_scenario,
        source="web",
        user_id=user_id,
        username=username,
    )
    record_id = rec.get("id")

    return {
        "code": 0,
        "msg": "总结生成成功",
        "data": summary_data,
        "record_id": record_id,
        "doc_url": doc_url,
        "bot_res": bot_res,
        "markdown": summarizer.format_to_markdown(summary_data),
    }


class MinutesRequest(BaseModel):
    minute_url: str
    scenario: str = "general"


@app.post("/api/minutes/summarize")
async def summarize_minutes(request: Request, req: MinutesRequest):
    user = get_auth_user(request)
    user_id = user["id"] if user else "u_admin"
    username = user.get("display_name") or user.get("username", "admin") if user else "管理员"

    feishu = get_feishu_client(user_id)
    minute_token = feishu.extract_minute_token(req.minute_url)
    
    transcript = ""
    try:
        info = feishu.get_minute_info(minute_token)
        transcript = info.get("transcript") or info.get("title") or ""
    except Exception as e:
        logger.info(f"飞书妙记 API 抓取: {e}，将采用标准转写流处理")

    if not transcript:
        pipeline = get_audio_pipeline(user_id)
        transcript = pipeline.transcribe("feishu_minutes.wav")

    summarizer = get_ai_summarizer(user_id)
    summary_data = summarizer.summarize(transcript, scenario=req.scenario)

    try:
        get_card_bridge().push_meeting_summary_to_card(summary_data)
    except Exception:
        pass

    from .history_manager import save_meeting_record
    rec = save_meeting_record(
        summary_data,
        doc_url="",
        scenario=req.scenario,
        source="minutes",
        user_id=user_id,
        username=username,
    )

    return {
        "code": 0,
        "msg": "飞书妙记总结完成",
        "data": summary_data,
        "record_id": rec.get("id"),
        "markdown": summarizer.format_to_markdown(summary_data),
    }


@app.get("/api/history")
async def list_history(request: Request):
    from .history_manager import get_meeting_history
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    is_admin = (user and user.get("role") == "admin")
    records = get_meeting_history(user_id=user_id, is_admin=is_admin)
    return {"code": 0, "data": records}


@app.delete("/api/history/{record_id}")
async def remove_history_record(request: Request, record_id: str):
    from .history_manager import delete_meeting_record
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    is_admin = (user and user.get("role") == "admin")
    ok = delete_meeting_record(record_id, user_id=user_id, is_admin=is_admin)
    if ok:
        return {"code": 0, "msg": "记录删除成功"}
    return {"code": -1, "msg": "未找到指定记录或无权限删除"}


@app.post("/api/history/clear")
async def clear_all_history(request: Request):
    from .history_manager import clear_meeting_history
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    is_admin = (user and user.get("role") == "admin")
    clear_meeting_history(user_id=user_id, is_admin=is_admin)
    return {"code": 0, "msg": "历史归档已清空"}


@app.get("/api/bots")
async def get_bot_channels_api(request: Request):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    cfg = get_config(user_id)
    return {"code": 0, "data": cfg.bot_channels}


@app.post("/api/bots")
async def save_bot_channels_api(request: Request, body: dict):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    channels = body.get("channels", [])
    save_config({"bot_channels": channels}, user_id=user_id)
    return {"code": 0, "msg": "机器人推送渠道配置已保存！"}


@app.post("/api/bots/test")
async def test_bot_channel_api(body: dict):
    from .bot_notifier import BotNotifier
    channel = body.get("channel", {})
    res = BotNotifier.send_test_message(channel)
    return res


@app.post("/api/bots/broadcast")
async def broadcast_bot_api(request: Request, body: dict):
    from .bot_notifier import BotNotifier
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    cfg = get_config(user_id)
    summary_data = body.get("summary_data", {})
    doc_url = body.get("doc_url", "")
    results = BotNotifier.broadcast(cfg.bot_channels, summary_data, doc_url=doc_url)
    return {"code": 0, "msg": "已向所有启用的机器人渠道发送推送！", "data": results}


@app.post("/api/feishu/push_bot")
async def push_to_bot(request: Request, summary_data: dict):
    from .bot_notifier import BotNotifier
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    cfg = get_config(user_id)
    doc_url = summary_data.get("doc_url", "")
    results = BotNotifier.broadcast(cfg.bot_channels, summary_data, doc_url=doc_url)
    return {"code": 0, "msg": "已向启用的多机器人渠道广播推送！", "data": results}


@app.post("/api/feishu/create_doc")
async def create_doc(request: Request, summary_data: dict):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    feishu = get_feishu_client(user_id)
    try:
        doc_url = feishu.create_feishu_doc(summary_data)
        record_id = summary_data.get("record_id") or summary_data.get("id")
        if record_id:
            from .history_manager import update_record_doc_url
            update_record_doc_url(record_id, doc_url, user_id=user_id)
        return {"code": 0, "msg": "飞书云文档创建成功！", "data": {"doc_url": doc_url}}
    except Exception as e:
        return {"code": -1, "msg": f"创建云文档失败: {str(e)}"}


@app.post("/api/feishu/sync_bitable")
async def sync_bitable(request: Request, summary_data: dict):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    feishu = get_feishu_client(user_id)
    todos = summary_data.get("todos", [])
    title = summary_data.get("title", "会议")
    count = feishu.sync_todos_to_bitable(todos, title)
    return {"code": 0, "msg": f"已成功同步 {count} 条待办到飞书多维表格"}


@app.get("/api/card/status")
async def get_card_status():
    return {"code": 0, "data": get_card_bridge().get_screen_state()}


@app.post("/api/card/event")
async def trigger_card_event(body: dict):
    event_name = body.get("event", "")
    return get_card_bridge().trigger_event(event_name)


@app.post("/api/card/mode")
async def set_card_mode(body: dict):
    mode_id = body.get("mode_id", "meeting")
    if mode_id == "menu":
        get_card_bridge().enter_menu()
    else:
        get_card_bridge().enter_mode(mode_id)
    return {"code": 0, "msg": f"卡片已切换至模式: {mode_id}", "data": get_card_bridge().get_screen_state()}


@app.get("/api/firmware/download")
async def download_firmware_bin():
    from fastapi.responses import FileResponse
    bin_path = Path(r"c:\Users\p\Desktop\ai 卡片\firmware\FoloToy-AI-Passport-full.bin")
    if bin_path.exists():
        return FileResponse(path=str(bin_path), filename="FoloToy-AI-Passport-full.bin", media_type="application/octet-stream")
    return {"code": -1, "msg": "固件文件未找到"}


@app.get("/api/firmware/download_zip")
async def download_firmware_zip():
    from fastapi.responses import FileResponse
    zip_path = Path(r"c:\Users\p\Desktop\ai 卡片\firmware\AI-Passport-Firmware-Package.zip")
    if zip_path.exists():
        return FileResponse(path=str(zip_path), filename="AI-Passport-Firmware-Package.zip", media_type="application/zip")
    return {"code": -1, "msg": "固件压缩包未找到"}


@app.get("/api/nfc/cards")
async def get_nfc_cards():
    cb = get_card_bridge()
    return {
        "code": 0,
        "data": {
            "cards": cb.nfc_cards,
            "selected_index": cb.nfc_selected_index,
            "is_swiping": cb.nfc_swiping,
        }
    }


@app.post("/api/nfc/cards")
async def add_nfc_card(body: dict):
    name = body.get("name", "未命名门禁卡")
    uid = body.get("uid", "8A:3F:12:C9")
    card_type = body.get("type", "Mifare Classic 1K")
    cb = get_card_bridge()
    new_card = cb.add_nfc_card(name, uid, card_type)
    return {"code": 0, "msg": f"已成功添加门禁卡: {name}", "data": new_card}


@app.delete("/api/nfc/cards/{card_id}")
async def remove_nfc_card(card_id: str):
    cb = get_card_bridge()
    ok = cb.delete_nfc_card(card_id)
    if ok:
        return {"code": 0, "msg": "门禁卡已删除"}
    return {"code": -1, "msg": "未找到指定卡片"}


@app.post("/api/nfc/swipe")
async def trigger_nfc_swipe_api():
    cb = get_card_bridge()
    cb.trigger_nfc_swipe()
    return {"code": 0, "msg": "已触发 NFC 模拟刷卡通行", "data": cb.get_screen_state()}


@app.post("/api/nfc/select")
async def select_nfc_card_api(body: dict):
    idx = body.get("index", 0)
    cb = get_card_bridge()
    if 0 <= idx < len(cb.nfc_cards):
        cb.nfc_selected_index = idx
        cb.render_and_send_frame()
        return {"code": 0, "msg": "已切换选中的门禁卡", "data": cb.get_screen_state()}
    return {"code": -1, "msg": "无效的卡片序号"}


@app.get("/api/audio/status")
async def get_audio_status():
    cb = get_card_bridge()
    return {
        "code": 0,
        "data": {
            "is_playing": cb.bt_is_playing,
            "volume": cb.bt_volume,
            "connected_device": cb.bt_connected_device,
            "current_track": cb.bt_playlist[cb.bt_track_index % len(cb.bt_playlist)],
            "track_index": cb.bt_track_index,
            "playlist": cb.bt_playlist,
            "playback_seconds": cb.bt_playback_seconds,
        }
    }


@app.post("/api/audio/toggle")
async def toggle_audio_play():
    cb = get_card_bridge()
    cb.toggle_bt_audio()
    return {"code": 0, "msg": "音频状态已切换", "data": cb.get_screen_state()}


@app.post("/api/audio/next")
async def next_audio_track():
    cb = get_card_bridge()
    cb.next_bt_track()
    return {"code": 0, "msg": "已切换下一首", "data": cb.get_screen_state()}


@app.post("/api/audio/prev")
async def prev_audio_track():
    cb = get_card_bridge()
    cb.prev_bt_track()
    return {"code": 0, "msg": "已切换上一首", "data": cb.get_screen_state()}


@app.post("/api/audio/volume")
async def set_audio_volume_api(body: dict):
    vol = int(body.get("volume", 75))
    cb = get_card_bridge()
    cb.set_bt_volume(vol)
    return {"code": 0, "msg": f"音量已设置为 {vol}%", "data": cb.get_screen_state()}


@app.post("/api/audio/speak_summary")
async def speak_summary_audio_api(body: dict):
    title = body.get("title")
    cb = get_card_bridge()
    cb.speak_summary_tts(title)
    return {"code": 0, "msg": "已通过蓝牙/卡片扬声器开始语音播报会议纪要", "data": cb.get_screen_state()}


@app.post("/api/card/push")
async def push_to_card(summary_data: dict):
    get_card_bridge().push_meeting_summary_to_card(summary_data)
    return {"code": 0, "msg": "已向所有连接的 AI Passport 卡片/模拟器广播纪要数据"}


@app.get("/api/feishu/test")
@app.post("/api/feishu/test")
async def test_feishu(request: Request):
    user = get_auth_user(request)
    user_id = user["id"] if user else None
    
    app_id = ""
    app_secret = ""
    if request.method == "POST":
        try:
            body = await request.json()
            app_id = body.get("app_id", "").strip()
            app_secret = body.get("app_secret", "").strip()
        except Exception:
            pass

    if app_id and app_secret:
        feishu = FeishuClient(app_id=app_id, app_secret=app_secret)
    else:
        feishu = get_feishu_client(user_id)

    try:
        token = feishu.get_tenant_access_token(force_refresh=True)
        return {"code": 0, "msg": f"✔ 飞书应用连接成功！已获取 Tenant Token (前缀: {token[:10]}...)"}
    except Exception as e:
        return {"code": -1, "msg": f"飞书连接失败: {str(e)} (请检查 App ID 与 App Secret 是否正确)"}


# ================= 🤖 sillyGirl 风格双向事件回调与 Webhook 接收入口 =================

@app.post("/api/webhook/feishu")
async def webhook_feishu_inbound(request: Request):
    """飞书开放平台事件订阅入口 (支持 URL 校验 challenge 与群聊事件监听)"""
    try:
        data = await request.json()
    except Exception:
        body = await request.body()
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return {"code": -1, "msg": "Invalid JSON"}

    # 1. 飞书开放平台配置校验 (challenge 握手)
    if "challenge" in data:
        return {"challenge": data["challenge"]}

    # 2. 事件解析 (im.message.receive_v1)
    from .robot_dispatcher import RobotDispatcher
    event = data.get("event", {})
    msg_obj = event.get("message", {})
    sender_obj = event.get("sender", {})

    sender_id = sender_obj.get("sender_id", {}).get("open_id", "feishu_user")
    sender_name = sender_obj.get("sender_id", {}).get("user_id", "飞书用户")
    chat_id = msg_obj.get("chat_id", "")

    # 解析文本内容
    text_content = ""
    if msg_obj.get("message_type") == "text":
        try:
            content_json = json.loads(msg_obj.get("content", "{}"))
            text_content = content_json.get("text", "")
        except Exception:
            text_content = msg_obj.get("content", "")

    if text_content:
        reply_res = RobotDispatcher.handle_incoming_message(
            platform="feishu",
            sender_id=sender_id,
            sender_name=sender_name,
            message_text=text_content,
            chat_id=chat_id,
            raw_event=data,
        )
        # 飞书官方自建应用在群内/私聊中执行正式回复
        message_id = msg_obj.get("message_id")
        feishu = get_feishu_client()
        if message_id and feishu.app_id and feishu.app_secret:
            try:
                reply_text = reply_res.get("content", "")
                feishu.reply_message(message_id, reply_text)
                logger.info(f"✔ 飞书自建应用已向群聊回复消息 (message_id: {message_id})")
            except Exception as e:
                logger.warning(f"飞书自建应用回复失败: {e}")

        return {"code": 0, "reply": reply_res}

    return {"code": 0, "msg": "Event received"}


@app.post("/api/webhook/dingtalk")
async def webhook_dingtalk_inbound(request: Request):
    """钉钉群机器人 Outgoing Webhook 监听入口"""
    data = await request.json()
    from .robot_dispatcher import RobotDispatcher

    text = data.get("text", {}).get("content", "").strip()
    sender_nick = data.get("senderNick", "钉钉用户")
    sender_id = data.get("senderStaffId", data.get("senderId", "ding_user"))
    chat_id = data.get("conversationId", "")

    reply_res = RobotDispatcher.handle_incoming_message(
        platform="dingtalk",
        sender_id=sender_id,
        sender_name=sender_nick,
        message_text=text,
        chat_id=chat_id,
        raw_event=data,
    )

    # 构造钉钉响应
    if reply_res.get("type") == "markdown":
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": reply_res.get("title", "会议总结"),
                "text": reply_res.get("content", ""),
            },
        }
    else:
        return {
            "msgtype": "text",
            "text": {
                "content": reply_res.get("content", ""),
            },
        }


@app.post("/api/webhook/wecom")
async def webhook_wecom_inbound(request: Request):
    """企业微信机器人回调入口"""
    data = await request.json()
    from .robot_dispatcher import RobotDispatcher

    text = data.get("text", {}).get("content", "") or data.get("content", "")
    sender_name = data.get("from", {}).get("name", "企业微信用户")
    sender_id = data.get("from", {}).get("id", "wecom_user")

    reply_res = RobotDispatcher.handle_incoming_message(
        platform="wecom",
        sender_id=sender_id,
        sender_name=sender_name,
        message_text=text,
        raw_event=data,
    )
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": reply_res.get("content", ""),
        },
    }


@app.post("/api/webhook/telegram")
async def webhook_telegram_inbound(request: Request):
    """Telegram Bot Webhook 监听入口"""
    data = await request.json()
    from .robot_dispatcher import RobotDispatcher
    from .bot_notifier import BotNotifier

    msg = data.get("message", {})
    text = msg.get("text", "")
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    from_user = msg.get("from", {})
    sender_name = from_user.get("first_name", "Telegram User")
    sender_id = str(from_user.get("id", "tg_user"))

    if text and chat_id:
        reply_res = RobotDispatcher.handle_incoming_message(
            platform="telegram",
            sender_id=sender_id,
            sender_name=sender_name,
            message_text=text,
            chat_id=chat_id,
            raw_event=data,
        )
        return {"code": 0, "reply": reply_res}

    return {"ok": True}


@app.post("/api/webhook/onebot")
async def webhook_onebot_inbound(request: Request):
    """QQ / OneBot (v11) HTTP 消息上报入口"""
    data = await request.json()
    from .robot_dispatcher import RobotDispatcher

    post_type = data.get("post_type")
    if post_type == "message":
        raw_msg = data.get("raw_message", "")
        sender = data.get("sender", {})
        sender_name = sender.get("nickname", sender.get("card", "QQ用户"))
        user_id = str(data.get("user_id", ""))
        group_id = str(data.get("group_id", ""))

        reply_res = RobotDispatcher.handle_incoming_message(
            platform="onebot",
            sender_id=user_id,
            sender_name=sender_name,
            message_text=raw_msg,
            chat_id=group_id,
            raw_event=data,
        )
        return {"reply": reply_res.get("content", "")}

    return {"status": "ok"}


@app.post("/api/webhook/custom")
async def webhook_custom_inbound(request: Request, body: dict):
    """通用自定义 Webhook 双向交互入口"""
    from .robot_dispatcher import RobotDispatcher

    text = body.get("message", body.get("text", body.get("content", "")))
    sender_name = body.get("sender_name", "外部调用方")
    sender_id = body.get("sender_id", "external")

    reply_res = RobotDispatcher.handle_incoming_message(
        platform="custom",
        sender_id=sender_id,
        sender_name=sender_name,
        message_text=text,
        raw_event=body,
    )
    return {"code": 0, "data": reply_res}


@app.get("/api/buckets")
async def get_buckets_api():
    """获取所有 Bucket 动态配置"""
    from .robot_dispatcher import BucketStore
    return {"code": 0, "data": BucketStore.get_all()}


@app.post("/api/buckets")
async def set_bucket_api(body: dict):
    """动态写入 Bucket 配置项"""
    from .robot_dispatcher import BucketStore
    bucket = body.get("bucket", "default")
    key = body.get("key", "")
    val = body.get("value", "")
    if not key:
        return {"code": -1, "msg": "Key 不能为空"}
    BucketStore.set(bucket, key, val)
    return {"code": 0, "msg": f"已成功更新 {bucket}.{key}"}

# ================= 📱 微信 ClawBot 扫码登录与控制接口 =================

@app.get("/api/clawbot/qrcode")
async def get_clawbot_qrcode(api_url: Optional[str] = None, token: Optional[str] = None):
    from .wechat_clawbot import get_wechat_bot
    bot = get_wechat_bot()
    return bot.fetch_qr_code(base_api_url=api_url, token=token)


@app.get("/api/clawbot/status")
async def get_clawbot_status():
    from .wechat_clawbot import get_wechat_bot
    bot = get_wechat_bot()
    return bot.check_login_status()




@app.post("/api/clawbot/logout")
async def logout_clawbot():
    from .wechat_clawbot import get_wechat_bot
    bot = get_wechat_bot()
    return bot.logout()


@app.post("/api/webhook/clawbot")
async def webhook_clawbot_inbound(request: Request):
    """微信 ClawBot / GeWe 消息上报 Webhook 监听入口"""
    data = await request.json()
    from .robot_dispatcher import RobotDispatcher
    from .wechat_clawbot import get_wechat_bot

    msg_data = data.get("Data", data)
    from_wxid = msg_data.get("FromWxid", msg_data.get("from_wxid", "wx_user"))
    to_wxid = msg_data.get("ToWxid", msg_data.get("to_wxid", ""))
    msg_type = msg_data.get("MsgType", 1)
    content_text = str(msg_data.get("Content", msg_data.get("content", ""))).strip()

    # 判断是否为群聊消息 (以 @chatroom 结尾)
    is_group = from_wxid.endswith("@chatroom")
    sender_id = from_wxid if not is_group else msg_data.get("FinalFromWxid", from_wxid)
    chat_id = from_wxid if is_group else None

    if content_text:
        reply_res = RobotDispatcher.handle_incoming_message(
            platform="wechat",
            sender_id=sender_id,
            sender_name="微信好友" if not is_group else "群成员",
            message_text=content_text,
            chat_id=chat_id,
            raw_event=data,
        )

        reply_content = reply_res.get("content", "")
        # 自动回发微信消息
        bot = get_wechat_bot()
        target = from_wxid
        bot.send_message(target, reply_content)

        return {"code": 0, "reply": reply_content}

    return {"code": 0, "msg": "WeChat message received"}

@app.post("/api/dingtalk/test_stream")
async def test_dingtalk_stream_endpoint(body: dict):
    """测试钉钉 Stream 模式 Client ID 与 Client Secret 连通性"""
    client_id = body.get("client_id", "").strip()
    client_secret = body.get("client_secret", "").strip()
    from .dingtalk_stream import get_dingtalk_stream
    dt = get_dingtalk_stream()
    return dt.test_connection(client_id, client_secret)

# ================= 🐧 QQ 机器人 (OneBot HTTP/WS 反向长连接) =================

@app.post("/api/onebot/test_connection")
async def test_onebot_connection_endpoint(body: dict):
    """测试 OneBot HTTP API 连通性与机器人登录状态"""
    api_url = (body.get("http_api_url") or "http://127.0.0.1:3000").rstrip("/")
    token = body.get("access_token", "").strip()

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        url = f"{api_url}/get_login_info"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "ok" or data.get("retcode") == 0:
                user_id = data.get("data", {}).get("user_id", "未知")
                nickname = data.get("data", {}).get("nickname", "QQ机器人")
                return {
                    "code": 0,
                    "msg": f"✔ QQ 机器人连接成功！当前在线账号: {nickname} ({user_id})",
                    "data": data.get("data", {})
                }
            return {"code": 0, "msg": "✔ 接口连通正常", "raw": data}
    except Exception as e:
        return {"code": -1, "msg": f"连接 OneBot 接口失败: {str(e)} (请确认 NapCat / Lagrange / Go-CQHTTP 是否已在 {api_url} 启动)"}


@app.websocket("/ws/onebot")
async def websocket_onebot_reverse(websocket: WebSocket):
    """OneBot 反向 WebSocket 长连接监听入口 (NapCat / Lagrange 配置反向 WS 即可免公网直接互通)"""
    await websocket.accept()
    logger.info("✔ OneBot 反向 WebSocket 客户端已建立连接！")
    try:
        while True:
            msg_raw = await websocket.receive_text()
            try:
                data = json.loads(msg_raw)
                post_type = data.get("post_type", "")
                
                # 处理群聊/私聊消息
                if post_type == "message":
                    msg_type = data.get("message_type", "group")
                    sender_id = str(data.get("user_id", ""))
                    sender_name = data.get("sender", {}).get("nickname", "QQ用户")
                    raw_msg = data.get("raw_message", data.get("message", ""))
                    group_id = data.get("group_id")

                    from .robot_dispatcher import RobotDispatcher
                    reply_res = RobotDispatcher.handle_incoming_message(
                        platform="qq",
                        sender_id=sender_id,
                        sender_name=sender_name,
                        message_text=raw_msg,
                        chat_id=str(group_id) if group_id else None,
                        raw_event=data
                    )

                    reply_text = reply_res.get("content", "")
                    if reply_text:
                        if msg_type == "group":
                            await websocket.send_text(json.dumps({
                                "action": "send_group_msg",
                                "params": {"group_id": group_id, "message": reply_text}
                            }))
                        else:
                            await websocket.send_text(json.dumps({
                                "action": "send_private_msg",
                                "params": {"user_id": int(sender_id) if sender_id.isdigit() else sender_id, "message": reply_text}
                            }))

            except Exception as pe:
                logger.debug(f"OneBot WS 报文处理: {pe}")
    except WebSocketDisconnect:
        logger.info("OneBot 反向 WebSocket 连接断开")
    except Exception as e:
        logger.warning(f"OneBot WS 异常: {e}")
