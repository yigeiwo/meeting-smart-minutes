# -*- coding: utf-8 -*-
import os
import json
import time
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("db_engine")

_engine = None
_is_connected = False


def get_db_url(cfg: Any = None) -> str:
    if cfg is None:
        from .config import get_config
        cfg = get_config()

    if getattr(cfg, "database_url", "").strip():
        return cfg.database_url.strip()

    # 默认开启 PostgreSQL 企业数据库
    user = getattr(cfg, "pg_user", "postgres") or "postgres"
    pwd = getattr(cfg, "pg_password", "") or ""
    host = getattr(cfg, "pg_host", "127.0.0.1") or "127.0.0.1"
    port = getattr(cfg, "pg_port", 5432) or 5432
    db = getattr(cfg, "pg_database", "feishu_meeting") or "feishu_meeting"
    return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"


def test_pg_connection(pg_config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from sqlalchemy import create_engine, text

        db_url = (pg_config.get("database_url") or "").strip()
        if not db_url:
            user = (pg_config.get("pg_user") or "postgres").strip()
            pwd = pg_config.get("pg_password") or ""
            host = (pg_config.get("pg_host") or "127.0.0.1").strip()
            port = pg_config.get("pg_port") or 5432
            db = (pg_config.get("pg_database") or "feishu_meeting").strip()
            db_url = f"postgresql://{user}:{pwd}@{host}:{port}/{db}"

        start_time = time.time()
        test_engine = create_engine(db_url, connect_args={"connect_timeout": 5})
        with test_engine.connect() as conn:
            res = conn.execute(text("SELECT version();")).fetchone()
            latency = int((time.time() - start_time) * 1000)
            pg_ver = str(res[0]) if res else "PostgreSQL"
            return {
                "code": 0,
                "msg": f"PostgreSQL 连接成功！(耗时 {latency}ms) - 版本: {pg_ver[:60]}",
                "version": pg_ver,
                "latency_ms": latency,
            }
    except UnicodeDecodeError:
        return {
            "code": -1,
            "msg": "PostgreSQL 连接失败: 目标主机拒绝连接或无法访问 (请检查 127.0.0.1:5432 端口与 PostgreSQL 服务状态)",
        }
    except Exception as e:
        err_msg = ""
        try:
            err_msg = str(e)
        except Exception:
            err_msg = "认证失败或网络连接超时"
        return {
            "code": -1,
            "msg": f"PostgreSQL 连接失败: {err_msg}",
        }


# ================= 🗄️ 企业级 13 大数据表 DDL 结构定义 =================

ENTERPRISE_DDL_STATEMENTS = [
    # 1. 多租户组织与企业团队表 (organizations)
    """
    CREATE TABLE IF NOT EXISTS organizations (
        id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        code VARCHAR(64) UNIQUE NOT NULL,
        logo_url TEXT,
        feishu_tenant_key VARCHAR(128),
        status VARCHAR(32) DEFAULT 'active',
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 2. 用户与安全鉴权表 (users)
    """
    CREATE TABLE IF NOT EXISTS users (
        id VARCHAR(64) PRIMARY KEY,
        org_id VARCHAR(64) DEFAULT 'org_default',
        username VARCHAR(64) UNIQUE NOT NULL,
        display_name VARCHAR(128) NOT NULL,
        email VARCHAR(128),
        phone VARCHAR(32),
        avatar_url TEXT,
        password_hash VARCHAR(256) NOT NULL,
        salt VARCHAR(64) NOT NULL,
        role VARCHAR(32) DEFAULT 'user',
        status VARCHAR(32) DEFAULT 'active',
        last_login_at VARCHAR(64),
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 3. 分布式会话与安全令牌表 (sessions)
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token VARCHAR(128) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        ip_address VARCHAR(64),
        user_agent TEXT,
        created_at DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL
    );
    """,

    # 4. 用户专属独立 AI / 飞书配置表 (user_configs)
    """
    CREATE TABLE IF NOT EXISTS user_configs (
        user_id VARCHAR(64) PRIMARY KEY,
        config_json TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,

    # 5. 企业 AI 大模型服务商与权重路由表 (ai_providers - 对标 NewAPI)
    """
    CREATE TABLE IF NOT EXISTS ai_providers (
        id VARCHAR(64) PRIMARY KEY,
        org_id VARCHAR(64) DEFAULT 'org_default',
        name VARCHAR(128) NOT NULL,
        provider_type VARCHAR(64) NOT NULL,
        base_url TEXT NOT NULL,
        api_key_encrypted TEXT,
        model_name VARCHAR(128) NOT NULL,
        available_models_json TEXT,
        temperature DOUBLE PRECISION DEFAULT 0.3,
        max_tokens INTEGER DEFAULT 4096,
        weight INTEGER DEFAULT 100,
        is_active BOOLEAN DEFAULT TRUE,
        latency_ms INTEGER DEFAULT 0,
        status VARCHAR(32) DEFAULT 'healthy',
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 6. AI 调用与 Token 消耗审计流水表 (ai_call_logs - 对标 NewAPI 计费审计)
    """
    CREATE TABLE IF NOT EXISTS ai_call_logs (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64),
        provider_id VARCHAR(64),
        model VARCHAR(128),
        scenario VARCHAR(64),
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        latency_ms INTEGER DEFAULT 0,
        status VARCHAR(32) DEFAULT 'success',
        error_msg TEXT,
        created_at VARCHAR(64)
    );
    """,

    # 7. 多渠道机器人与推送网关配置表 (bot_channels - 对标 sillyGirl)
    """
    CREATE TABLE IF NOT EXISTS bot_channels (
        id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64),
        channel_type VARCHAR(64) NOT NULL,
        name VARCHAR(128) NOT NULL,
        is_enabled BOOLEAN DEFAULT FALSE,
        webhook_url TEXT,
        secret_or_token TEXT,
        extra_params_json TEXT,
        status VARCHAR(32) DEFAULT 'active',
        last_push_at VARCHAR(64),
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 8. 机器人推送历史与送达审计表 (bot_push_logs)
    """
    CREATE TABLE IF NOT EXISTS bot_push_logs (
        id VARCHAR(64) PRIMARY KEY,
        channel_id VARCHAR(64),
        channel_type VARCHAR(64),
        meeting_record_id VARCHAR(64),
        payload_json TEXT,
        response_json TEXT,
        status VARCHAR(32) DEFAULT 'success',
        latency_ms INTEGER DEFAULT 0,
        created_at VARCHAR(64)
    );
    """,

    # 9. 会议纪要与飞书知识资产核心归档表 (meeting_records)
    """
    CREATE TABLE IF NOT EXISTS meeting_records (
        id VARCHAR(64) PRIMARY KEY,
        org_id VARCHAR(64) DEFAULT 'org_default',
        user_id VARCHAR(64) NOT NULL,
        username VARCHAR(128) NOT NULL,
        title VARCHAR(256) NOT NULL,
        date VARCHAR(64),
        duration VARCHAR(64),
        participants_json TEXT,
        audio_file_path TEXT,
        transcript_text TEXT,
        summary_overview TEXT,
        decisions_json TEXT,
        todos_json TEXT,
        topics_json TEXT,
        follow_ups_json TEXT,
        doc_url TEXT,
        doc_id VARCHAR(128),
        bitable_app_token VARCHAR(128),
        bitable_table_id VARCHAR(128),
        scenario VARCHAR(64) DEFAULT 'general',
        source VARCHAR(64) DEFAULT 'web',
        status VARCHAR(32) DEFAULT 'completed',
        raw_data_json TEXT,
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 10. 会议行动项与 Todo 独立任务追踪表 (meeting_action_items)
    """
    CREATE TABLE IF NOT EXISTS meeting_action_items (
        id VARCHAR(64) PRIMARY KEY,
        meeting_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(64),
        task_content TEXT NOT NULL,
        owner_name VARCHAR(128) DEFAULT '待定',
        owner_email VARCHAR(128),
        owner_feishu_id VARCHAR(128),
        priority VARCHAR(32) DEFAULT 'P1',
        due_date VARCHAR(64),
        status VARCHAR(32) DEFAULT 'pending',
        feishu_bitable_record_id VARCHAR(128),
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 11. AI Passport 智能胸卡硬件设备资产表 (hardware_devices - 对标 FoloToy)
    """
    CREATE TABLE IF NOT EXISTS hardware_devices (
        id VARCHAR(64) PRIMARY KEY,
        device_sn VARCHAR(128) UNIQUE NOT NULL,
        mac_address VARCHAR(64),
        device_name VARCHAR(128) NOT NULL,
        bound_user_id VARCHAR(64),
        firmware_version VARCHAR(64),
        battery_level INTEGER DEFAULT 100,
        status VARCHAR(32) DEFAULT 'idle',
        last_heartbeat_at VARCHAR(64),
        created_at VARCHAR(64),
        updated_at VARCHAR(64)
    );
    """,

    # 12. 硬件设备通信与录音事件日志表 (hardware_device_logs)
    """
    CREATE TABLE IF NOT EXISTS hardware_device_logs (
        id VARCHAR(64) PRIMARY KEY,
        device_sn VARCHAR(128) NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        raw_payload_json TEXT,
        created_at VARCHAR(64)
    );
    """,

    # 13. sillyGirl 动态 Bucket 键值参数存储表 (system_buckets)
    """
    CREATE TABLE IF NOT EXISTS system_buckets (
        id VARCHAR(64) PRIMARY KEY,
        bucket VARCHAR(64) NOT NULL,
        key VARCHAR(128) NOT NULL,
        value_json TEXT NOT NULL,
        description TEXT,
        updated_at VARCHAR(64),
        CONSTRAINT uq_bucket_key UNIQUE (bucket, key)
    );
    """,

    # 索引优化 (Indexes)
    """CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id);""",
    """CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);""",
    """CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);""",
    """CREATE INDEX IF NOT EXISTS idx_ai_logs_user ON ai_call_logs(user_id);""",
    """CREATE INDEX IF NOT EXISTS idx_ai_logs_time ON ai_call_logs(created_at);""",
    """CREATE INDEX IF NOT EXISTS idx_meeting_user ON meeting_records(user_id);""",
    """CREATE INDEX IF NOT EXISTS idx_meeting_time ON meeting_records(created_at);""",
    """CREATE INDEX IF NOT EXISTS idx_action_items_meeting ON meeting_action_items(meeting_id);""",
    """CREATE INDEX IF NOT EXISTS idx_hardware_devices_sn ON hardware_devices(device_sn);""",
    """CREATE INDEX IF NOT EXISTS idx_buckets_name ON system_buckets(bucket);"""
]


def init_pg_schema(engine):
    """企业级 DDL 自动化建表与索引初始化"""
    from sqlalchemy import text
    with engine.begin() as conn:
        for stmt in ENTERPRISE_DDL_STATEMENTS:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                logger.warning(f"执行 DDL 语句异常: {e}")

    # 初始化企业级默认种子数据 (Seed Data)
    init_seed_data(engine)
    logger.info("✔ PostgreSQL 企业级 13 大数据表结构与索引校验初始化完成！")


def init_seed_data(engine):
    """自动灌入企业级初始预设数据（默认组织、管理员账号、预设AI模型池、9大机器人渠道）"""
    from sqlalchemy import text
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        # 1. 默认组织
        conn.execute(
            text("""
                INSERT INTO organizations (id, name, code, status, created_at, updated_at)
                VALUES ('org_default', '智能数字化办公研发组', 'DEFAULT_ORG', 'active', :now, :now)
                ON CONFLICT (id) DO NOTHING
            """),
            {"now": now_str}
        )

        # 2. 默认超级管理员 (admin / admin123) 与开发者账号
        import hashlib
        salt = "6bc6f22832f02e6b"
        pwd_hash = hashlib.sha256(("admin123" + salt).encode("utf-8")).hexdigest()
        conn.execute(
            text("""
                INSERT INTO users (id, org_id, username, display_name, email, password_hash, salt, role, status, created_at)
                VALUES ('u_admin', 'org_default', 'admin', '系统管理员', 'admin@feishu.company', :pwd, :salt, 'admin', 'active', :now)
                ON CONFLICT (id) DO NOTHING
            """),
            {"pwd": pwd_hash, "salt": salt, "now": now_str}
        )

        salt_dev = "fa054f10c1be6eac"
        pwd_hash_dev = hashlib.sha256(("feishu123" + salt_dev).encode("utf-8")).hexdigest()
        conn.execute(
            text("""
                INSERT INTO users (id, org_id, username, display_name, email, password_hash, salt, role, status, created_at)
                VALUES ('u_c6eec125', 'org_default', 'feishu_user', '飞书开发者', 'dev@feishu.com', :pwd, :salt, 'user', 'active', :now)
                ON CONFLICT (id) DO NOTHING
            """),
            {"pwd": pwd_hash_dev, "salt": salt_dev, "now": now_str}
        )

        # 3. 默认硬件设备预注册
        conn.execute(
            text("""
                INSERT INTO hardware_devices (id, device_sn, device_name, firmware_version, battery_level, status, created_at)
                VALUES ('dev_sim_01', 'SN_PASSPORT_ESP32_001', 'AI Passport 智能胸卡 (模拟器/实体卡)', 'v2.1.0-lark', 95, 'idle', :now)
                ON CONFLICT (id) DO NOTHING
            """),
            {"now": now_str}
        )

        # 4. 默认系统 Bucket 配置 (sillyGirl 动态参数)
        buckets = [
            ("system", "version", "2.5.0-enterprise", "系统版本号"),
            ("system", "environment", "production", "运行环境"),
            ("ai", "default_scenario", "general", "默认会议场景模板"),
            ("notify", "auto_broadcast", "true", "录音结束自动多渠道广播"),
            ("hardware", "bridge_port", "5566", "卡片通信端口"),
        ]
        for b_name, b_key, b_val, b_desc in buckets:
            conn.execute(
                text("""
                    INSERT INTO system_buckets (id, bucket, key, value_json, description, updated_at)
                    VALUES (:id, :bucket, :key, :val, :desc, :now)
                    ON CONFLICT (bucket, key) DO NOTHING
                """),
                {"id": f"b_{b_name}_{b_key}", "bucket": b_name, "key": b_key, "val": json.dumps(b_val), "desc": b_desc, "now": now_str}
            )


def get_pg_engine():
    global _engine, _is_connected
    if _engine is not None:
        return _engine

    from .config import get_config
    cfg = get_config()
    db_url = get_db_url(cfg)
    if not db_url:
        return None

    try:
        from sqlalchemy import create_engine
        _engine = create_engine(
            db_url,
            pool_size=20,
            max_overflow=40,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        init_pg_schema(_engine)
        _is_connected = True
        logger.info("✔ 成功连接至 PostgreSQL 企业数据库并初始化连接池与全量表结构")
        return _engine
    except Exception as e:
        logger.warning(f"PostgreSQL 连接异常 ({e})，系统已自动平滑回退至本地安全高可用模式")
        _engine = None
        _is_connected = False
        return None


def reset_pg_engine():
    global _engine, _is_connected
    if _engine:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _is_connected = False
