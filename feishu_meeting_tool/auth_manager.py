# -*- coding: utf-8 -*-
import os
import re
import json
import time
import uuid
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("auth_manager")

USERS_FILE = Path("records/users.json")
SESSIONS_FILE = Path("records/sessions.json")


def _ensure_files():
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        salt = os.urandom(8).hex()
        pwd_hash = _hash_password("admin123", salt)
        default_users = [
            {
                "id": "u_admin",
                "username": "admin",
                "display_name": "系统管理员",
                "email": "admin@feishu.company",
                "password_hash": pwd_hash,
                "salt": salt,
                "role": "admin",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        ]
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_users, f, ensure_ascii=False, indent=2)

    if not SESSIONS_FILE.exists():
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def get_all_users() -> List[Dict[str, Any]]:
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, username, display_name, email, password_hash, salt, role, created_at FROM users")
                ).fetchall()
                if rows:
                    return [
                        {
                            "id": r[0],
                            "username": r[1],
                            "display_name": r[2] or r[1],
                            "email": r[3] or "",
                            "password_hash": r[4],
                            "salt": r[5],
                            "role": r[6],
                            "created_at": str(r[7]),
                        }
                        for r in rows
                    ]
    except Exception as e:
        logger.debug(f"从 PostgreSQL 读取用户列表失败，回退到本地存储: {e}")

    _ensure_files()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    for u in get_all_users():
        if u["id"] == user_id:
            return u
    return None


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    u_norm = (username or "").strip().lower()
    for u in get_all_users():
        if u["username"].strip().lower() == u_norm:
            return u
    return None


def register_user(username: str, password: str, display_name: Optional[str] = None, email: Optional[str] = None, role: str = "user") -> Dict[str, Any]:
    username = (username or "").strip()
    password = (password or "").strip()
    display_name = (display_name or "").strip() or username
    email = (email or "").strip()

    # 校验用户名格式：3-20 位，字母、数字、下划线、减号
    if not username or len(username) < 3 or len(username) > 20:
        return {"code": -1, "msg": "用户名长度需在 3 到 20 个字符之间"}
    if not re.match(r"^[a-zA-Z0-9_\-]+$", username):
        return {"code": -1, "msg": "用户名只能包含英文字母、数字、下划线(_)或连字符(-)"}

    # 校验密码格式
    if not password or len(password) < 6:
        return {"code": -1, "msg": "密码长度至少需要 6 个字符"}

    # 检查用户名唯一性
    if get_user_by_username(username):
        return {"code": -1, "msg": f"账号 '{username}' 已被占用，请更换其他用户名"}

    user_id = f"u_{uuid.uuid4().hex[:8]}"
    salt = os.urandom(8).hex()
    pwd_hash = _hash_password(password, salt)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    new_user = {
        "id": user_id,
        "username": username,
        "display_name": display_name,
        "email": email,
        "password_hash": pwd_hash,
        "salt": salt,
        "role": role,
        "created_at": now_str,
    }

    # 优先持久化至 PostgreSQL
    pg_saved = False
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO users (id, username, display_name, email, password_hash, salt, role, created_at)
                        VALUES (:id, :username, :display_name, :email, :password_hash, :salt, :role, :created_at)
                    """),
                    new_user,
                )
                pg_saved = True
                logger.info(f"✔ 用户已持久化至 PostgreSQL users: {username} ({user_id})")
    except Exception as e:
        logger.warning(f"写入 PostgreSQL users 失败，写入本地 JSON: {e}")

    # 同时写入本地 JSON 文件做双重备份
    _ensure_files()
    users = []
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception:
        users = []
    users.append(new_user)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    return {
        "code": 0,
        "msg": "注册成功",
        "user": {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "email": email,
            "role": role,
            "created_at": now_str,
        },
    }


def upsert_user(username: str, password: str, display_name: Optional[str] = None,
                email: Optional[str] = None, role: str = "admin") -> Dict[str, Any]:
    """创建或更新账号 (管理员建号 / 重置密码用)。

    - 账号不存在: 走正常注册流程创建；
    - 账号已存在: 重置其密码、角色与显示名，并同步 PostgreSQL 与本地 JSON 两份存储。
    返回 {"code": 0, "msg": ..., "user": {...}, "created": bool}
    """
    username = (username or "").strip()
    password = (password or "").strip()

    if not username or len(username) < 3 or len(username) > 20:
        return {"code": -1, "msg": "用户名长度需在 3 到 20 个字符之间"}
    if not re.match(r"^[a-zA-Z0-9_\-]+$", username):
        return {"code": -1, "msg": "用户名只能包含英文字母、数字、下划线(_)或连字符(-)"}
    if not password or len(password) < 6:
        return {"code": -1, "msg": "密码长度至少需要 6 个字符"}

    existing = get_user_by_username(username)
    if not existing:
        res = register_user(username=username, password=password,
                            display_name=display_name, email=email, role=role)
        if res.get("code") == 0:
            res["created"] = True
        return res

    user_id = existing["id"]
    salt = os.urandom(8).hex()
    pwd_hash = _hash_password(password, salt)
    new_display = (display_name or "").strip() or existing.get("display_name") or username
    new_email = existing.get("email", "") if email is None else email
    new_role = role or existing.get("role", "user")

    updated = {
        "id": user_id,
        "username": username,
        "display_name": new_display,
        "email": new_email,
        "password_hash": pwd_hash,
        "salt": salt,
        "role": new_role,
        "created_at": existing.get("created_at", time.strftime("%Y-%m-%d %H:%M:%S")),
    }

    # 1) 更新 PostgreSQL
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        UPDATE users
                           SET password_hash = :password_hash,
                               salt = :salt,
                               role = :role,
                               display_name = :display_name,
                               email = :email
                         WHERE id = :id
                    """),
                    updated,
                )
            logger.info(f"✔ 已在 PostgreSQL 更新账号: {username} ({user_id})")
    except Exception as e:
        logger.warning(f"更新 PostgreSQL users 失败: {e}")

    # 2) 同步本地 JSON 备份
    _ensure_files()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
    except Exception:
        users = []
    hit = False
    for i, u in enumerate(users):
        if u.get("id") == user_id or (u.get("username", "").strip().lower() == username.lower()):
            users[i] = updated
            hit = True
            break
    if not hit:
        users.append(updated)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    return {
        "code": 0,
        "msg": "账号已更新",
        "created": False,
        "user": {
            "id": user_id,
            "username": username,
            "display_name": new_display,
            "email": new_email,
            "role": new_role,
            "created_at": updated["created_at"],
        },
    }


def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_user_by_username(username)
    if not user:
        return None
    pwd_hash = _hash_password(password, user["salt"])
    if pwd_hash == user["password_hash"]:
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": user.get("display_name") or user["username"],
            "email": user.get("email", ""),
            "role": user.get("role", "user"),
            "created_at": user.get("created_at", ""),
        }
    return None


def create_session(user_id: str, days: int = 7) -> str:
    token = f"sess_{uuid.uuid4().hex}"
    expires_at = time.time() + days * 86400

    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO sessions (token, user_id, expires_at) VALUES (:token, :user_id, :expires_at)"),
                    {"token": token, "user_id": user_id, "expires_at": expires_at},
                )
    except Exception as e:
        logger.debug(f"写入 PostgreSQL sessions 异常: {e}")

    _ensure_files()
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
    except Exception:
        sessions = {}
    sessions[token] = {
        "user_id": user_id,
        "expires_at": expires_at,
    }
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

    return token


def validate_session(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None

    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT user_id, expires_at FROM sessions WHERE token = :token"),
                    {"token": token},
                ).fetchone()
                if row:
                    uid, exp = row[0], row[1]
                    if exp > time.time():
                        return get_user_by_id(uid)
    except Exception:
        pass

    _ensure_files()
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
            sess = sessions.get(token)
            if sess and sess.get("expires_at", 0) > time.time():
                return get_user_by_id(sess.get("user_id"))
    except Exception:
        pass

    return None


def delete_session(token: str):
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM sessions WHERE token = :token"), {"token": token})
    except Exception:
        pass

    _ensure_files()
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        if token in sessions:
            del sessions[token]
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
