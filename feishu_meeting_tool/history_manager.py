# -*- coding: utf-8 -*-
import json
import time
import uuid
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger("history_manager")
HISTORY_FILE = Path("records/meeting_history.json")


def _ensure_dir():
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not HISTORY_FILE.exists():
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def get_all_records() -> List[Dict[str, Any]]:
    # 1. 优先从 PostgreSQL 读取
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.connect() as conn:
                rows = conn.execute(
                    text("""
                        SELECT id, user_id, username, title, date, duration, created_at,
                               doc_url, scenario, source, summary_overview,
                               decisions_json, todos_json, topics_json, follow_ups_json, raw_data_json
                        FROM meeting_records
                        ORDER BY created_at DESC
                    """)
                ).fetchall()
                if rows:
                    results = []
                    for r in rows:
                        results.append({
                            "id": r[0],
                            "user_id": r[1],
                            "username": r[2],
                            "title": r[3],
                            "date": r[4],
                            "duration": r[5],
                            "created_at": r[6],
                            "doc_url": r[7] or "",
                            "scenario": r[8],
                            "source": r[9],
                            "summary_overview": r[10] or "",
                            "decisions": json.loads(r[11]) if r[11] else [],
                            "todos": json.loads(r[12]) if r[12] else [],
                            "topics": json.loads(r[13]) if r[13] else [],
                            "follow_ups": json.loads(r[14]) if r[14] else [],
                            "raw_data": json.loads(r[15]) if r[15] else {},
                        })
                    return results
    except Exception as e:
        logger.debug(f"从 PostgreSQL 查询会议历史失败，回退到本地 JSON: {e}")

    # 2. 回退读取本地 JSON
    _ensure_dir()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取会议历史文件失败: {e}")
        return []


def get_meeting_history(user_id: Optional[str] = None, is_admin: bool = False) -> List[Dict[str, Any]]:
    all_recs = get_all_records()
    if is_admin or not user_id:
        return all_recs
    return [r for r in all_recs if r.get("user_id") == user_id or not r.get("user_id")]


def save_meeting_record(
    summary_data: Dict[str, Any],
    doc_url: str = "",
    scenario: str = "general",
    source: str = "audio",
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    transcript_text: str = "",
) -> Dict[str, Any]:
    rec_id = summary_data.get("id") or summary_data.get("record_id") or f"rec_{uuid.uuid4().hex[:10]}"
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    final_doc_url = doc_url or summary_data.get("doc_url", "")
    final_username = username or "系统管理员"

    record = {
        "id": rec_id,
        "user_id": user_id or "u_admin",
        "username": final_username,
        "title": summary_data.get("title", f"会议纪要_{now_str[:10]}"),
        "date": summary_data.get("date", now_str),
        "duration": summary_data.get("duration", "未识别"),
        "participants": summary_data.get("participants", "未识别"),
        "created_at": now_str,
        "doc_url": final_doc_url,
        "scenario": scenario,
        "source": source,
        "summary_overview": summary_data.get("summary_overview", ""),
        "transcript_text": transcript_text or "",
        "decisions": summary_data.get("decisions", []),
        "todos": summary_data.get("todos", []),
        "topics": summary_data.get("topics", []),
        "follow_ups": summary_data.get("follow_ups", []),
        "raw_data": summary_data,
    }

    # 1. 优先持久化至 PostgreSQL
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO meeting_records (
                            id, user_id, username, title, date, duration, created_at,
                            doc_url, scenario, source, summary_overview, transcript_text,
                            decisions_json, todos_json, topics_json, follow_ups_json, raw_data_json
                        ) VALUES (
                            :id, :user_id, :username, :title, :date, :duration, :created_at,
                            :doc_url, :scenario, :source, :summary_overview, :transcript_text,
                            :decisions_json, :todos_json, :topics_json, :follow_ups_json, :raw_data_json
                        )
                        ON CONFLICT (id) DO UPDATE SET
                            doc_url = EXCLUDED.doc_url,
                            summary_overview = EXCLUDED.summary_overview,
                            transcript_text = EXCLUDED.transcript_text,
                            decisions_json = EXCLUDED.decisions_json,
                            todos_json = EXCLUDED.todos_json,
                            raw_data_json = EXCLUDED.raw_data_json
                    """),
                    {
                        "id": record["id"],
                        "user_id": record["user_id"],
                        "username": record["username"],
                        "title": record["title"],
                        "date": record["date"],
                        "duration": record["duration"],
                        "created_at": record["created_at"],
                        "doc_url": record["doc_url"],
                        "scenario": record["scenario"],
                        "source": record["source"],
                        "summary_overview": record["summary_overview"],
                        "transcript_text": record["transcript_text"],
                        "decisions_json": json.dumps(record["decisions"], ensure_ascii=False),
                        "todos_json": json.dumps(record["todos"], ensure_ascii=False),
                        "topics_json": json.dumps(record["topics"], ensure_ascii=False),
                        "follow_ups_json": json.dumps(record["follow_ups"], ensure_ascii=False),
                        "raw_data_json": json.dumps(record["raw_data"], ensure_ascii=False),
                    },
                )
                logger.info(f"✔ 会议记录已持久化到 PostgreSQL meeting_records: {rec_id}")
    except Exception as e:
        logger.warning(f"写入 PostgreSQL meeting_records 失败，写入本地 JSON: {e}")

    # 2. 写入本地 JSON
    _ensure_dir()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        records = []

    exists = False
    for i, r in enumerate(records):
        if r.get("id") == rec_id:
            records[i] = record
            exists = True
            break
    if not exists:
        records.insert(0, record)

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入本地会议历史文件失败: {e}")

    return record


def update_record_doc_url(record_id: str, doc_url: str, user_id: Optional[str] = None):
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE meeting_records SET doc_url = :doc_url WHERE id = :id"),
                    {"doc_url": doc_url, "id": record_id},
                )
    except Exception:
        pass

    _ensure_dir()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
        for r in records:
            if r.get("id") == record_id:
                r["doc_url"] = doc_url
                break
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"更新记录文档 URL 失败: {e}")


def delete_meeting_record(record_id: str, user_id: Optional[str] = None, is_admin: bool = False) -> bool:
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                if is_admin or not user_id:
                    conn.execute(text("DELETE FROM meeting_records WHERE id = :id"), {"id": record_id})
                else:
                    conn.execute(
                        text("DELETE FROM meeting_records WHERE id = :id AND user_id = :uid"),
                        {"id": record_id, "uid": user_id},
                    )
    except Exception:
        pass

    _ensure_dir()
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
        new_records = [
            r for r in records
            if r.get("id") != record_id or (not is_admin and user_id and r.get("user_id") != user_id)
        ]
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(new_records, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"删除会议历史记录失败: {e}")
        return False


def clear_meeting_history(user_id: Optional[str] = None, is_admin: bool = False):
    try:
        from .db_engine import get_pg_engine
        engine = get_pg_engine()
        if engine:
            from sqlalchemy import text
            with engine.begin() as conn:
                if is_admin or not user_id:
                    conn.execute(text("DELETE FROM meeting_records"))
                else:
                    conn.execute(text("DELETE FROM meeting_records WHERE user_id = :uid"), {"uid": user_id})
    except Exception:
        pass

    _ensure_dir()
    try:
        if is_admin or not user_id:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        else:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
            records = [r for r in records if r.get("user_id") != user_id]
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"清空会议历史记录失败: {e}")
