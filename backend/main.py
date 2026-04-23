"""
FastAPI 主程式
提供前台 API（新聞查詢、統計）與管理 API（設定、來源、爬蟲控制）
"""

import csv
import io
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, case, and_, extract
from sqlalchemy.orm import Session

from .database import (
    get_db, init_db,
    NewsArticle, NewsSource, Setting, CrawlerLog
)
from .schemas import (
    NewsArticleOut, NewsListResponse, DashboardStats, StatsItem,
    SettingOut, SettingUpdate, BulkSettingUpdate,
    NewsSourceOut, NewsSourceCreate, NewsSourceUpdate,
    CrawlerLogOut, SystemStats,
)
from .config import get_setting, set_setting
from .auth import (
    verify_password, hash_password,
    create_session, validate_session, delete_session,
    get_or_create_password_hash,
)
from .collector import run_crawler, reclassify_article, reclassify_all_articles, cleanup_old_articles
from .historical_collector import run_historical_collection
from .scheduler import (
    start_scheduler, stop_scheduler,
    update_schedule, get_next_run_time, get_schedule_summary
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 應用程式生命週期
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("初始化資料庫...")
    init_db()
    # 確保管理員密碼已初始化
    _, is_new = get_or_create_password_hash()
    if is_new:
        logger.warning("⚠️  管理員密碼已設為預設值 'admin'，請登入後立即至「一般設定」更改！")
    # 若尚未設定多時段排程，寫入預設值（一天 3 次：08:00, 14:00, 20:00）
    if not get_setting("crawler_schedule_hours", ""):
        set_setting("crawler_schedule_hours", "8,14,20")
        logger.info("排程預設值已初始化：08:00、14:00、20:00")
    logger.info("啟動排程器...")
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="資安新聞分析平台",
    description="每日自動收集資安新聞，AI 分類後提供查詢與分析",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS（允許前端靜態頁面呼叫）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# Health Check（不需 DB，給 Railway 用）
# ──────────────────────────────────────────────

@app.get("/health", tags=["系統"])
def health():
    return {"status": "ok"}


# ──────────────────────────────────────────────
# 管理介面驗證 Middleware
# ──────────────────────────────────────────────

# 不需驗證的路徑
_AUTH_EXEMPT = {"/api/admin/login", "/api/admin/logout"}

@app.middleware("http")
async def admin_auth_middleware(request: Request, call_next):
    path = request.url.path
    # 保護所有 /api/admin/* 路由（登入/登出除外）
    if path.startswith("/api/admin/") and path not in _AUTH_EXEMPT:
        token = request.cookies.get("admin_session")
        if not validate_session(token):
            return JSONResponse(
                {"detail": "未授權，請先登入", "redirect": "/admin/login"},
                status_code=401,
            )
    return await call_next(request)


# ──────────────────────────────────────────────
# 登入 / 登出 API
# ──────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


@app.post("/api/admin/login", tags=["驗證"])
def admin_login(body: LoginRequest):
    """管理員登入，成功後設定 Session Cookie"""
    stored_hash, _ = get_or_create_password_hash()
    if not verify_password(body.password, stored_hash):
        raise HTTPException(status_code=401, detail="密碼錯誤")

    token = create_session()
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,       # JS 無法讀取，防 XSS
        samesite="strict",   # 防 CSRF
        max_age=24 * 3600,
        path="/",
    )
    return response


@app.post("/api/admin/logout", tags=["驗證"])
def admin_logout(request: Request):
    """登出，清除 Session Cookie"""
    token = request.cookies.get("admin_session")
    delete_session(token)
    response = JSONResponse({"status": "ok"})
    response.delete_cookie("admin_session", path="/")
    return response


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/admin/change-password", tags=["驗證"])
def change_password(body: ChangePasswordRequest):
    """更改管理員密碼"""
    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密碼至少需要 6 個字元")

    stored_hash, _ = get_or_create_password_hash()
    if not verify_password(body.current_password, stored_hash):
        raise HTTPException(status_code=401, detail="目前密碼錯誤")

    new_hash = hash_password(body.new_password)
    set_setting("admin_password_hash", new_hash)
    return {"status": "ok", "message": "密碼已更改"}


# ──────────────────────────────────────────────
# 前台 API：新聞查詢
# ──────────────────────────────────────────────

@app.get("/api/news", response_model=NewsListResponse, tags=["前台"])
def list_news(
    q:       Optional[str] = Query(None, description="關鍵字搜尋（標題/摘要）"),
    attack_type:     Optional[str] = Query(None, description="攻擊類型篩選"),
    region:          Optional[str] = Query(None, description="地區篩選"),
    affected_system: Optional[str] = Query(None, description="受影響系統篩選"),
    severity:        Optional[str] = Query(None, description="嚴重程度篩選"),
    days:            Optional[int] = Query(None, description="最近 N 天"),
    page:            int = Query(1, ge=1),
    size:            int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(NewsArticle)

    if q:
        like = f"%{q}%"
        query = query.filter(
            (NewsArticle.title.ilike(like)) |
            (NewsArticle.summary.ilike(like))
        )
    if attack_type:
        query = query.filter(NewsArticle.attack_type == attack_type)
    if region:
        query = query.filter(NewsArticle.region == region)
    if affected_system:
        query = query.filter(NewsArticle.affected_system == affected_system)
    if severity:
        query = query.filter(NewsArticle.severity == severity)
    if days:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(NewsArticle.collected_date >= cutoff)

    total = query.count()
    items = (
        query.order_by(NewsArticle.published_date.desc().nullslast(), NewsArticle.collected_date.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    return NewsListResponse(total=total, page=page, size=size, items=items)


@app.get("/api/news/export", tags=["前台"])
def export_news_csv(
    q:               Optional[str] = Query(None),
    attack_type:     Optional[str] = Query(None),
    region:          Optional[str] = Query(None),
    affected_system: Optional[str] = Query(None),
    severity:        Optional[str] = Query(None),
    days:            Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """匯出符合篩選條件的新聞為 CSV（UTF-8 BOM，Excel 可直接開啟）"""
    query = db.query(NewsArticle)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (NewsArticle.title.ilike(like)) | (NewsArticle.summary.ilike(like))
        )
    if attack_type:
        query = query.filter(NewsArticle.attack_type == attack_type)
    if region:
        query = query.filter(NewsArticle.region == region)
    if affected_system:
        query = query.filter(NewsArticle.affected_system == affected_system)
    if severity:
        query = query.filter(NewsArticle.severity == severity)
    if days:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.filter(NewsArticle.collected_date >= cutoff)

    articles = (
        query.order_by(
            NewsArticle.published_date.desc().nullslast(),
            NewsArticle.collected_date.desc()
        ).all()
    )

    # 產生 CSV（加 BOM 讓 Excel 正確顯示中文）
    output = io.StringIO()
    output.write('\ufeff')  # UTF-8 BOM
    writer = csv.writer(output)
    writer.writerow(['標題', '來源', '發布日期', '收集日期', '攻擊類型', '地區', '受影響系統', '嚴重程度', '摘要', '連結'])
    for a in articles:
        writer.writerow([
            a.title or '',
            a.source_name or '',
            a.published_date.strftime('%Y-%m-%d %H:%M') if a.published_date else '',
            a.collected_date.strftime('%Y-%m-%d %H:%M') if a.collected_date else '',
            a.attack_type or '',
            a.region or '',
            a.affected_system or '',
            a.severity or '',
            a.summary or '',
            a.url or '',
        ])

    filename = f"cybersec_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.get("/api/news/{article_id}", response_model=NewsArticleOut, tags=["前台"])
def get_news(article_id: int, db: Session = Depends(get_db)):
    article = db.query(NewsArticle).filter_by(id=article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="新聞不存在")
    return article


@app.get("/api/stats", response_model=DashboardStats, tags=["前台"])
def get_stats(db: Session = Depends(get_db)):
    today = datetime.utcnow().date()
    month_start = today.replace(day=1)

    # ── 數字統計（全部用 SQL COUNT，不載入資料到記憶體）──
    total = db.query(func.count(NewsArticle.id)).scalar() or 0

    today_count = db.query(func.count(NewsArticle.id)).filter(
        func.date(NewsArticle.collected_date) == today
    ).scalar() or 0

    critical_count = db.query(func.count(NewsArticle.id)).filter(
        NewsArticle.severity == "嚴重"
    ).scalar() or 0

    today_critical = db.query(func.count(NewsArticle.id)).filter(
        NewsArticle.severity == "嚴重",
        func.date(NewsArticle.collected_date) == today
    ).scalar() or 0

    month_critical = db.query(func.count(NewsArticle.id)).filter(
        NewsArticle.severity == "嚴重",
        NewsArticle.collected_date >= month_start
    ).scalar() or 0

    # ── 分布統計（GROUP BY）──
    def group_top(column, n=8) -> list[StatsItem]:
        rows = (
            db.query(column, func.count(NewsArticle.id).label("cnt"))
            .filter(column.isnot(None), column != "")
            .group_by(column)
            .order_by(func.count(NewsArticle.id).desc())
            .limit(n)
            .all()
        )
        return [StatsItem(label=label, count=cnt) for label, cnt in rows]

    attack_types  = group_top(NewsArticle.attack_type)
    regions       = group_top(NewsArticle.region)
    systems       = group_top(NewsArticle.affected_system)
    severity_dist = group_top(NewsArticle.severity)

    # ── 近 7 天趨勢（COALESCE published_date, collected_date，GROUP BY date）──
    cutoff = today - timedelta(days=6)
    eff_date = func.date(
        func.coalesce(NewsArticle.published_date, NewsArticle.collected_date)
    )
    trend_rows = (
        db.query(eff_date.label("d"), func.count(NewsArticle.id).label("cnt"))
        .filter(eff_date >= cutoff)
        .group_by("d")
        .order_by("d")
        .all()
    )
    trend_map = {str(row.d): row.cnt for row in trend_rows}
    weekly = [
        StatsItem(
            label=(today - timedelta(days=6 - i)).strftime("%m/%d"),
            count=trend_map.get(str(today - timedelta(days=6 - i)), 0)
        )
        for i in range(7)
    ]

    return DashboardStats(
        total_articles    = total,
        today_articles    = today_count,
        critical_articles = critical_count,
        today_critical    = today_critical,
        month_critical    = month_critical,
        attack_types      = attack_types,
        regions           = regions,
        affected_systems  = systems,
        severity_dist     = severity_dist,
        weekly_trend      = weekly,
    )


# ──────────────────────────────────────────────
# 趨勢查詢（獨立端點，支援 7 / 30 天）
# ──────────────────────────────────────────────

@app.get("/api/stats/trend", response_model=list[StatsItem], tags=["前台"])
def get_trend(days: int = Query(7, ge=7, le=30), db: Session = Depends(get_db)):
    today  = datetime.utcnow().date()
    cutoff = today - timedelta(days=days - 1)
    eff_date = func.date(
        func.coalesce(NewsArticle.published_date, NewsArticle.collected_date)
    )
    rows = (
        db.query(eff_date.label("d"), func.count(NewsArticle.id).label("cnt"))
        .filter(eff_date >= cutoff)
        .group_by("d")
        .order_by("d")
        .all()
    )
    trend_map = {str(r.d): r.cnt for r in rows}
    return [
        StatsItem(
            label=(today - timedelta(days=days - 1 - i)).strftime("%m/%d"),
            count=trend_map.get(str(today - timedelta(days=days - 1 - i)), 0)
        )
        for i in range(days)
    ]


# ──────────────────────────────────────────────
# 管理 API：設定
# ──────────────────────────────────────────────

@app.get("/api/admin/settings", response_model=list[SettingOut], tags=["管理"])
def get_all_settings(db: Session = Depends(get_db)):
    return db.query(Setting).order_by(Setting.key).all()


@app.put("/api/admin/settings", tags=["管理"])
def update_settings(payload: BulkSettingUpdate, db: Session = Depends(get_db)):
    """批次更新設定"""
    for key, value in payload.settings.items():
        set_setting(key, str(value), db)

    # 若排程時間被修改，動態更新排程
    if "crawler_schedule_hours" in payload.settings or \
       "crawler_schedule_minute" in payload.settings or \
       "crawler_schedule_hour" in payload.settings:
        hours_str = get_setting("crawler_schedule_hours", "") or get_setting("crawler_schedule_hour", "8")
        minute    = int(get_setting("crawler_schedule_minute", "0"))
        update_schedule(hours_str, minute)

    return {"status": "ok", "message": "設定已儲存"}


# ──────────────────────────────────────────────
# 管理 API：新聞來源
# ──────────────────────────────────────────────

@app.get("/api/admin/sources", response_model=list[NewsSourceOut], tags=["管理"])
def list_sources(db: Session = Depends(get_db)):
    return db.query(NewsSource).order_by(NewsSource.priority, NewsSource.name).all()


@app.post("/api/admin/sources", response_model=NewsSourceOut, tags=["管理"])
def create_source(payload: NewsSourceCreate, db: Session = Depends(get_db)):
    if db.query(NewsSource).filter_by(url=payload.url).first():
        raise HTTPException(status_code=400, detail="此 URL 已存在")
    source = NewsSource(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@app.put("/api/admin/sources/{source_id}", response_model=NewsSourceOut, tags=["管理"])
def update_source(
    source_id: int, payload: NewsSourceUpdate, db: Session = Depends(get_db)
):
    source = db.query(NewsSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="來源不存在")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return source


@app.delete("/api/admin/sources/{source_id}", tags=["管理"])
def delete_source(source_id: int, db: Session = Depends(get_db)):
    source = db.query(NewsSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="來源不存在")
    db.delete(source)
    db.commit()
    return {"status": "ok"}


# ──────────────────────────────────────────────
# 管理 API：爬蟲控制
# ──────────────────────────────────────────────

_crawler_running = False

@app.post("/api/admin/crawler/run", tags=["管理"])
def trigger_crawler(background_tasks: BackgroundTasks):
    global _crawler_running
    if _crawler_running:
        return {"status": "running", "message": "爬蟲正在執行中，請稍候"}
    _crawler_running = True
    background_tasks.add_task(_run_crawler_task)
    return {"status": "started", "message": "爬蟲已啟動，請稍候查看執行紀錄"}


def _run_crawler_task():
    global _crawler_running
    try:
        run_crawler()
    finally:
        _crawler_running = False


@app.get("/api/admin/crawler/status", tags=["管理"])
def crawler_status():
    return {
        "running":          _crawler_running,
        "next_run":         get_next_run_time(),
        "schedule_summary": get_schedule_summary(),
    }


@app.get("/api/admin/crawler/logs", response_model=list[CrawlerLogOut], tags=["管理"])
def get_crawler_logs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    return (
        db.query(CrawlerLog)
        .order_by(CrawlerLog.run_at.desc())
        .limit(limit)
        .all()
    )


# ──────────────────────────────────────────────
# 管理 API：新聞管理
# ──────────────────────────────────────────────

@app.delete("/api/admin/news/{article_id}", tags=["管理"])
def delete_article(article_id: int, db: Session = Depends(get_db)):
    article = db.query(NewsArticle).filter_by(id=article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="新聞不存在")
    db.delete(article)
    db.commit()
    return {"status": "ok"}


@app.post("/api/admin/news/{article_id}/reclassify", tags=["管理"])
def reclassify(article_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(reclassify_article, article_id)
    return {"status": "started", "message": "重新分類已啟動"}


_reclassify_all_running = False

@app.post("/api/admin/news/reclassify-all", tags=["管理"])
def reclassify_all(background_tasks: BackgroundTasks):
    """重新分類資料庫中所有新聞（背景執行）"""
    global _reclassify_all_running
    if _reclassify_all_running:
        return {"status": "already_running", "message": "重新分類已在執行中，請稍後"}

    def _run():
        global _reclassify_all_running
        _reclassify_all_running = True
        try:
            result = reclassify_all_articles()
            logger.info(f"[全部重新分類] 完成：{result}")
        finally:
            _reclassify_all_running = False

    background_tasks.add_task(_run)
    return {"status": "started", "message": "全部重新分類已開始，請查看伺服器日誌追蹤進度"}


@app.get("/api/admin/news/reclassify-all/status", tags=["管理"])
def reclassify_all_status():
    """查詢全部重新分類是否執行中"""
    return {"running": _reclassify_all_running}


_historical_running = False

@app.post("/api/admin/crawler/historical", tags=["管理"])
def trigger_historical(
    background_tasks: BackgroundTasks,
    days: int = Query(30, ge=1, le=365, description="收集幾天的歷史數據"),
):
    """一次性歷史數據收集（不受每日數量上限限制）"""
    global _historical_running
    if _historical_running:
        return {"status": "running", "message": "歷史收集正在執行中，請稍候"}
    _historical_running = True
    background_tasks.add_task(_run_historical_task, days)
    return {
        "status":  "started",
        "message": f"開始收集近 {days} 天的歷史新聞（背景執行，依文章數量需時數分鐘至數十分鐘）",
    }


def _run_historical_task(days: int):
    global _historical_running
    try:
        run_historical_collection(days=days, verbose=False)
    finally:
        _historical_running = False


@app.get("/api/admin/crawler/historical/status", tags=["管理"])
def historical_status():
    return {"running": _historical_running}


@app.post("/api/admin/cleanup", tags=["管理"])
def manual_cleanup(db: Session = Depends(get_db)):
    from .config import get_crawler_config
    cfg = get_crawler_config()
    deleted = cleanup_old_articles(cfg["retention_days"])
    return {"status": "ok", "deleted": deleted, "message": f"已刪除 {deleted} 篇舊新聞"}


# ──────────────────────────────────────────────
# 管理 API：系統監控
# ──────────────────────────────────────────────

@app.get("/api/admin/system", response_model=SystemStats, tags=["管理"])
def system_stats(db: Session = Depends(get_db)):
    total    = db.query(NewsArticle).count()
    enabled  = db.query(NewsSource).filter_by(enabled=True).count()
    total_src= db.query(NewsSource).count()

    last_log = (
        db.query(CrawlerLog)
        .order_by(CrawlerLog.run_at.desc())
        .first()
    )

    # 估算資料量（每篇平均 2KB）
    size_kb    = total * 2
    size_label = f"約 {size_kb} KB" if size_kb < 1024 else f"約 {size_kb//1024} MB"

    return SystemStats(
        total_articles    = total,
        sources_enabled   = enabled,
        sources_total     = total_src,
        last_crawl        = last_log.run_at if last_log else None,
        last_crawl_status = last_log.status if last_log else None,
        db_size_estimate  = size_label,
    )


# ──────────────────────────────────────────────
# 靜態檔案（前端）
# ──────────────────────────────────────────────

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def serve_dashboard():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/admin/login", include_in_schema=False)
    def serve_login():
        return FileResponse(os.path.join(FRONTEND_DIR, "login.html"))

    @app.get("/admin", include_in_schema=False)
    def serve_admin(request: Request):
        """已登入才開放管理介面，否則導向登入頁"""
        token = request.cookies.get("admin_session")
        if not validate_session(token):
            return RedirectResponse(url="/admin/login", status_code=302)
        return FileResponse(os.path.join(FRONTEND_DIR, "admin.html"))
