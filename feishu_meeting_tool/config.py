# -*- coding: utf-8 -*-
import os
import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from .bot_notifier import DEFAULT_BOT_CHANNELS

ENV_PATH = Path(".env")
CONFIG_JSON_PATH = Path("config.json")

load_dotenv(dotenv_path=ENV_PATH)

DEFAULT_LLM_PROVIDERS = [
    {
        "id": "newapi",
        "name": "New API (中转网关)",
        "base_url": "https://your-newapi-domain.com/v1",
        "model": "gpt-4o",
        "available_models": [
            "gpt-4o",
            "gpt-4o-mini",
            "deepseek-chat",
            "deepseek-reasoner",
            "claude-3-7-sonnet-latest",
            "gemini-2.0-flash",
            "qwen3.8-max",
            "doubao-1.5-pro-32k",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "custom_openai",
        "name": "通用 OpenAI 格式",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "available_models": [
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
            "deepseek-chat",
            "claude-3-5-sonnet-latest",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "available_models": [
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "doubao",
        "name": "豆包 (Doubao)",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-1.5-pro-32k",
        "available_models": [
            "Doubao-Seed-2.1-pro",
            "doubao-1.5-pro-32k",
            "doubao-1.5-pro-256k",
            "doubao-1.5-thinking-32k",
            "doubao-1.5-lite-32k",
            "doubao-pro-32k",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "available_models": [
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
            "o1",
            "o1-mini",
            "gpt-4.5-preview",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "kimi",
        "name": "Kimi (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",
        "available_models": [
            "kimi-k3",
            "kimi-k2.7-code",
            "kimi-latest",
            "kimi-k1.5",
            "moonshot-v1-128k",
            "moonshot-v1-32k",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "qwen",
        "name": "通义千问 (Qwen)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.8-max",
        "available_models": [
            "qwen3.8-max",
            "qwen3.7-plus",
            "qwen-max-latest",
            "qwen-plus-latest",
            "qwq-32b-preview",
            "qwen2.5-72b-instruct",
            "qwen-turbo-latest",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "zhipu",
        "name": "智谱清言 (GLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "available_models": [
            "glm-4-plus",
            "glm-4-flash",
            "glm-4-long",
            "glm-zero-preview",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "claude",
        "name": "Claude (Anthropic)",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5",
        "available_models": [
            "claude-sonnet-5",
            "claude-opus-5",
            "claude-haiku-4-5",
            "claude-3-7-sonnet-latest",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
        ],
        "api_key": "",
        "temperature": 0.3,
    },
    {
        "id": "ollama",
        "name": "本地私有化 (Ollama)",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "deepseek-r1:8b",
        "available_models": [
            "deepseek-r1:8b",
            "deepseek-r1:14b",
            "deepseek-r1:32b",
            "qwen2.5:7b",
            "qwen2.5:14b",
            "llama3.3:70b",
        ],
        "api_key": "ollama",
        "temperature": 0.3,
    },
]


@dataclass
class AppConfig:
    # 飞书开放平台凭证
    feishu_app_id: str = field(default_factory=lambda: os.getenv("FEISHU_APP_ID", ""))
    feishu_app_secret: str = field(default_factory=lambda: os.getenv("FEISHU_APP_SECRET", ""))
    feishu_bot_webhook_url: str = field(default_factory=lambda: os.getenv("FEISHU_BOT_WEBHOOK_URL", ""))
    feishu_bot_secret: str = field(default_factory=lambda: os.getenv("FEISHU_BOT_SECRET", ""))
    feishu_doc_folder_token: str = field(default_factory=lambda: os.getenv("FEISHU_DOC_FOLDER_TOKEN", ""))
    feishu_bitable_app_token: str = field(default_factory=lambda: os.getenv("FEISHU_BITABLE_APP_TOKEN", ""))
    feishu_bitable_table_id: str = field(default_factory=lambda: os.getenv("FEISHU_BITABLE_TABLE_ID", ""))

    # 当前激活的 AI 模型配置
    active_llm_id: str = field(default_factory=lambda: os.getenv("ACTIVE_LLM_ID", "deepseek"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat"))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.3")))

    # 多 AI 模型配置列表
    llm_providers: List[Dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_LLM_PROVIDERS))

    # 语音转写 ASR 配置
    asr_provider: str = field(default_factory=lambda: os.getenv("ASR_PROVIDER", "whisper"))
    asr_api_key: str = field(default_factory=lambda: os.getenv("ASR_API_KEY", ""))
    asr_app_id: str = field(default_factory=lambda: os.getenv("ASR_APP_ID", ""))

    # 默认会议总结场景模板 (general, tech, prd, business, brainstorm)
    default_scenario: str = field(default_factory=lambda: os.getenv("DEFAULT_SCENARIO", "general"))

    # AI Passport 硬件桥接服务端口配置
    bridge_host: str = field(default_factory=lambda: os.getenv("BRIDGE_HOST", "0.0.0.0"))
    bridge_port: int = field(default_factory=lambda: int(os.getenv("BRIDGE_PORT", "5566")))
    serial_port: str = field(default_factory=lambda: os.getenv("SERIAL_PORT", ""))

    # 硬件胸卡 WebSocket 通道 (/ws/card) 访问令牌。
    # 为空表示不校验 (仅建议在局域网调试时使用); 公网部署务必设置,
    # 否则任何人只要能连上该路径就能伪造胸卡报文、注入会议音频。
    card_ws_token: str = field(default_factory=lambda: os.getenv("CARD_WS_TOKEN", ""))

    # 用户注册开关: 默认关闭 (内部系统), 需要开放注册时设 ALLOW_REGISTER=true
    allow_register: bool = field(default_factory=lambda: os.getenv("ALLOW_REGISTER", "false").lower() in ["true", "1", "yes"])

    # Web 服务器配置
    web_host: str = field(default_factory=lambda: os.getenv("WEB_HOST", "127.0.0.1"))
    web_port: int = field(default_factory=lambda: int(os.getenv("WEB_PORT", "8000")))

    # 录音保存目录
    records_dir: str = field(default_factory=lambda: os.getenv("RECORDS_DIR", "records"))

    # 多机器人推送渠道配置 (飞书 / 企微 / 钉钉 / Telegram / QQ OneBot / PushPlus / Server酱 / Bark 等)
    bot_channels: List[Dict[str, Any]] = field(default_factory=lambda: list(DEFAULT_BOT_CHANNELS))

    # PostgreSQL 数据库配置 (默认启用企业级关系型数据库架构)
    pg_enabled: bool = field(default_factory=lambda: os.getenv("PG_ENABLED", "true").lower() in ["true", "1", "yes"])
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", "127.0.0.1"))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_database: str = field(default_factory=lambda: os.getenv("PG_DATABASE", "feishu_meeting"))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "postgres"))
    pg_password: str = field(default_factory=lambda: os.getenv("PG_PASSWORD", ""))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))


USER_CONFIGS_DIR = Path("records/user_configs")


def get_config(user_id: Optional[str] = None) -> AppConfig:
    config = AppConfig()
    
    # 1. 先加载系统全局配置
    if CONFIG_JSON_PATH.exists():
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if hasattr(config, k):
                        setattr(config, k, v)
        except Exception:
            pass

    # 2. 若传入了 user_id，叠加该用户的专属配置 (优先从 PostgreSQL，其次从本地文件)
    if user_id:
        loaded = False
        try:
            from .db_engine import get_pg_engine
            engine = get_pg_engine()
            if engine:
                from sqlalchemy import text
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT config_json FROM user_configs WHERE user_id = :uid"),
                        {"uid": user_id},
                    ).fetchone()
                    if row and row[0]:
                        u_data = json.loads(row[0])
                        for k, v in u_data.items():
                            if hasattr(config, k):
                                setattr(config, k, v)
                        loaded = True
        except Exception:
            pass

        if not loaded:
            user_file = USER_CONFIGS_DIR / f"{user_id}.json"
            if user_file.exists():
                try:
                    with open(user_file, "r", encoding="utf-8") as f:
                        u_data = json.load(f)
                        for k, v in u_data.items():
                            if hasattr(config, k):
                                setattr(config, k, v)
                except Exception:
                    pass

    # Ensure llm_providers is populated
    if not config.llm_providers:
        config.llm_providers = list(DEFAULT_LLM_PROVIDERS)

    # Ensure bot_channels is populated
    if not config.bot_channels:
        config.bot_channels = list(DEFAULT_BOT_CHANNELS)
    else:
        # Merge any missing default channel
        existing_ids = {c.get("id") for c in config.bot_channels}
        for d_ch in DEFAULT_BOT_CHANNELS:
            if d_ch["id"] not in existing_ids:
                config.bot_channels.append(d_ch)

    # Sync legacy feishu bot webhook url
    if config.feishu_bot_webhook_url:
        for ch in config.bot_channels:
            if ch.get("id") == "feishu" and not ch.get("webhook_url"):
                ch["webhook_url"] = config.feishu_bot_webhook_url
                ch["secret"] = config.feishu_bot_secret

    return config


def save_config(config_dict: dict, user_id: Optional[str] = None) -> AppConfig:
    config = get_config(user_id)
    for k, v in config_dict.items():
        if hasattr(config, k):
            setattr(config, k, v)

    # 1. 保存到用户专属配置
    if user_id:
        # 同步写入 PostgreSQL
        try:
            from .db_engine import get_pg_engine
            engine = get_pg_engine()
            if engine:
                from sqlalchemy import text
                with engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO user_configs (user_id, config_json, updated_at)
                            VALUES (:uid, :cfg, CURRENT_TIMESTAMP)
                            ON CONFLICT (user_id) DO UPDATE SET config_json = :cfg, updated_at = CURRENT_TIMESTAMP;
                        """),
                        {"uid": user_id, "cfg": json.dumps(asdict(config), ensure_ascii=False)},
                    )
        except Exception:
            pass

        # 写入本地冗余保障
        USER_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        user_file = USER_CONFIGS_DIR / f"{user_id}.json"
        with open(user_file, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)
    else:
        # 保存全局配置
        CONFIG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(config), f, ensure_ascii=False, indent=2)

    return config
