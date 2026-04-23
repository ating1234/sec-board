"""
系統設定管理
從資料庫讀寫設定值
"""

from datetime import datetime
from sqlalchemy.orm import Session
from .database import SessionLocal, Setting


def get_setting(key: str, default: str = "") -> str:
    db = SessionLocal()
    try:
        row = db.query(Setting).filter_by(key=key).first()
        return row.value if row else default
    finally:
        db.close()


def set_setting(key: str, value: str, db: Session = None) -> None:
    close_after = False
    if db is None:
        db = SessionLocal()
        close_after = True
    try:
        row = db.query(Setting).filter_by(key=key).first()
        if row:
            row.value      = value
            row.updated_at = datetime.utcnow()
        else:
            db.add(Setting(key=key, value=value))
        db.commit()
    finally:
        if close_after:
            db.close()


def get_all_settings(db: Session) -> dict:
    rows = db.query(Setting).all()
    return {r.key: r.value for r in rows}


def get_crawler_config() -> dict:
    # 優先讀新設定 crawler_schedule_hours（逗號分隔，如 "8,14,20"）
    # 若不存在則 fallback 到舊版單一 hour 設定
    hours_str = get_setting("crawler_schedule_hours", "")
    if not hours_str:
        old_hour = get_setting("crawler_schedule_hour", "8")
        hours_str = old_hour  # 向下相容
    return {
        "schedule_hours":  hours_str,                                      # e.g. "8,14,20"
        "schedule_minute": int(get_setting("crawler_schedule_minute", "0")),
        "max_articles":    int(get_setting("max_articles_per_day",    "10")),
        "retention_days":  int(get_setting("retention_days",          "90")),
    }


def get_llm_config() -> dict:
    """讀取 LLM 設定（Groq 或 Gemini）"""
    provider = get_setting("llm_provider", "groq")
    return {
        "provider":         provider,
        "prompt_prefix":    get_setting("classification_prompt", ""),
        # Groq
        "groq_api_key":     get_setting("groq_api_key",  ""),
        "groq_model":       get_setting("groq_model",    "llama-3.3-70b-versatile"),
        # Gemini
        "gemini_api_key":   get_setting("gemini_api_key", ""),
        "gemini_model":     get_setting("gemini_model",   "gemini-2.0-flash"),
    }


# 向後相容（舊程式碼可能還在呼叫這個）
def get_gemini_config() -> dict:
    cfg = get_llm_config()
    return {
        "api_key":       cfg["gemini_api_key"],
        "model":         cfg["gemini_model"],
        "prompt_prefix": cfg["prompt_prefix"],
    }
