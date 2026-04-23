"""
資料庫模型與初始化
使用 SQLAlchemy ORM + PostgreSQL
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    Boolean, DateTime, Float, UniqueConstraint
)
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# 資料庫連線字串（可由環境變數覆蓋）
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/cybersec_db"
)
# Railway 提供的 URL 前綴為 postgres://，SQLAlchemy 需要 postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# ORM 模型
# ──────────────────────────────────────────────

class NewsArticle(Base):
    """資安新聞"""
    __tablename__ = "news_articles"

    id             = Column(Integer, primary_key=True, index=True)
    title          = Column(Text,    nullable=False)
    summary        = Column(Text)                          # AI 生成中文摘要
    url            = Column(Text,    unique=True, nullable=False)
    source_name    = Column(String(200))
    published_date = Column(DateTime)
    collected_date = Column(DateTime, default=datetime.utcnow)
    attack_type    = Column(String(100))   # 攻擊類型
    region         = Column(String(100))   # 地區
    affected_system= Column(String(100))   # 受影響系統
    severity       = Column(String(50))    # 嚴重程度
    raw_content    = Column(Text)          # 原始摘要文字


class NewsSource(Base):
    """新聞來源"""
    __tablename__ = "news_sources"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(200), nullable=False)
    url        = Column(Text,        nullable=False, unique=True)
    region     = Column(String(100))
    enabled    = Column(Boolean,     default=True)
    priority   = Column(Integer,     default=5)   # 1=最高（台灣/中國大陸優先）
    created_at = Column(DateTime,    default=datetime.utcnow)


class Setting(Base):
    """系統設定（Key-Value）"""
    __tablename__ = "settings"

    key         = Column(String(100), primary_key=True)
    value       = Column(Text,        nullable=False)
    description = Column(Text)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CrawlerLog(Base):
    """爬蟲執行紀錄"""
    __tablename__ = "crawler_logs"

    id                = Column(Integer, primary_key=True, index=True)
    run_at            = Column(DateTime, default=datetime.utcnow)
    status            = Column(String(50))   # success / partial / failed
    articles_collected= Column(Integer, default=0)
    sources_checked   = Column(Integer, default=0)
    error_message     = Column(Text)
    duration_seconds  = Column(Float)


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def get_db():
    """FastAPI 依賴注入用 DB Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建立資料表，並插入預設資料"""
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        _insert_default_settings(db)
        _insert_default_sources(db)
        db.commit()
    finally:
        db.close()


def _insert_default_settings(db):
    defaults = [
        ("crawler_schedule_hour",   "8",
         "爬蟲每日執行時間（小時，0-23）"),
        ("crawler_schedule_minute",  "0",
         "爬蟲每日執行時間（分鐘，0-59）"),
        ("max_articles_per_day",    "10",
         "每日最多收集新聞數量（1-50）"),
        # ── LLM 供應商設定 ──
        ("llm_provider",            "groq",
         "LLM 供應商（groq / gemini）"),
        # Groq
        ("groq_api_key",            "",
         "Groq API 金鑰（至 console.groq.com 申請，免費）"),
        ("groq_model",              "llama-3.3-70b-versatile",
         "Groq 模型（llama-3.3-70b-versatile / llama-3.1-8b-instant）"),
        # Gemini（備用）
        ("gemini_api_key",          "",
         "Google Gemini API 金鑰（至 aistudio.google.com 申請）"),
        ("gemini_model",            "gemini-2.0-flash",
         "Gemini 模型（gemini-2.0-flash / gemini-2.0-flash-lite）"),
        # 共用
        ("retention_days",          "90",
         "新聞保留天數，超過後自動清除"),
        ("classification_prompt",
         "你是資訊安全專家，請用繁體中文分析以下資安新聞，並以 JSON 格式回傳分類結果。",
         "AI 分類提示詞前綴（可調整分析風格）"),
    ]
    for key, value, desc in defaults:
        if not db.query(Setting).filter_by(key=key).first():
            db.add(Setting(key=key, value=value, description=desc))


def _insert_default_sources(db):
    sources = [
        # 台灣（priority=1，最高優先）
        ("iThome 資安",       "https://www.ithome.com.tw/rss",                "台灣",   1),
        ("TWCERT/CC",         "https://www.twcert.org.tw/rss",                "台灣",   1),
        # 中國大陸（priority=2）
        ("FreeBuf",           "https://www.freebuf.com/feed",                 "中國大陸", 2),
        ("安全客",             "https://www.anquanke.com/rss",                 "中國大陸", 2),
        # 全球（priority=3）
        ("BleepingComputer",  "https://www.bleepingcomputer.com/feed/",       "全球",   3),
        ("The Hacker News",   "https://feeds.feedburner.com/TheHackersNews",  "全球",   3),
        ("SecurityWeek",      "https://feeds.feedburner.com/securityweek",    "全球",   3),
        ("Krebs on Security", "https://krebsonsecurity.com/feed/",            "全球",   3),
        ("Dark Reading",      "https://www.darkreading.com/rss.xml",          "全球",   3),
        ("CISA Alerts",       "https://www.cisa.gov/uscert/ncas/alerts.xml",  "北美",   3),
    ]
    for name, url, region, priority in sources:
        if not db.query(NewsSource).filter_by(url=url).first():
            db.add(NewsSource(
                name=name, url=url, region=region,
                priority=priority, enabled=True
            ))
