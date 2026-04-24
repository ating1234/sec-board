"""
RSS 新聞爬蟲
每日抓取各來源的最新資安新聞，優先台灣和中國大陸，
避免重複，並呼叫 Gemini 分類後存入資料庫
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
import requests

from .database import SessionLocal, NewsArticle, NewsSource, CrawlerLog
from .config import get_crawler_config
from .classifier import classify_article

logger = logging.getLogger(__name__)

# HTTP 請求標頭（避免被擋）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; CybersecBot/1.0; "
        "+https://github.com/cybersec-dashboard)"
    )
}
FETCH_TIMEOUT = 15  # 秒


# ──────────────────────────────────────────────
# 主要入口
# ──────────────────────────────────────────────

def run_crawler() -> dict:
    """
    執行一次爬蟲任務
    回傳執行結果 dict
    """
    start_time = time.time()
    db = SessionLocal()
    log = CrawlerLog(run_at=datetime.utcnow(), status="running")
    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        cfg = get_crawler_config()
        max_articles = cfg["max_articles"]

        # 依優先順序取得啟用的來源
        sources = (
            db.query(NewsSource)
            .filter(NewsSource.enabled == True)
            .order_by(NewsSource.priority.asc(), NewsSource.name.asc())
            .all()
        )

        collected_today = _count_today_articles(db)
        remaining = max(0, max_articles - collected_today)

        if remaining == 0:
            _finish_log(log, db, "success", 0, len(sources),
                        start_time, "今日已達收集上限")
            return {"status": "success", "collected": 0, "message": "今日已達收集上限"}

        total_collected = 0
        sources_checked = 0

        for source in sources:
            if remaining <= 0:
                break

            sources_checked += 1
            logger.info(f"抓取來源：{source.name} ({source.url})")

            entries = _fetch_feed(source.url)
            if not entries:
                continue

            for entry in entries[:20]:  # 每個來源最多看 20 篇
                if remaining <= 0:
                    break

                url = _get_entry_url(entry)
                if not url:
                    continue

                # 檢查是否已存在
                if db.query(NewsArticle).filter_by(url=url).first():
                    continue

                title   = _clean_text(entry.get("title", "（無標題）"))
                content = _extract_content(entry)

                logger.info(f"  → 分類中：{title[:60]}")

                # 呼叫 Gemini 分類
                classification = classify_article(title, content, source.name)

                pub_date = _parse_date(entry)

                article = NewsArticle(
                    title          = title,
                    url            = url,
                    source_name    = source.name,
                    published_date = pub_date,
                    collected_date = datetime.utcnow(),
                    raw_content    = content[:5000],
                    attack_type    = classification.get("attack_type"),
                    region         = classification.get("region"),
                    affected_system= classification.get("affected_system"),
                    severity       = classification.get("severity"),
                    summary        = classification.get("summary"),
                )
                db.add(article)
                db.commit()

                total_collected += 1
                remaining -= 1
                time.sleep(1)  # 避免 Gemini API 速率限制

        _finish_log(log, db, "success", total_collected, sources_checked, start_time)
        logger.info(f"爬蟲完成：共收集 {total_collected} 篇")
        return {
            "status":    "success",
            "collected": total_collected,
            "sources":   sources_checked,
        }

    except Exception as e:
        logger.error(f"爬蟲發生錯誤：{e}", exc_info=True)
        _finish_log(log, db, "failed", 0, 0, start_time, str(e))
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def _fetch_feed(url: str) -> List[dict]:
    """抓取 RSS Feed，回傳 entries 列表"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        return feed.entries or []
    except requests.exceptions.SSLError as e:
        # SSL 驗證失敗：記錄錯誤並跳過，不允許降級繞過驗證
        logger.error(f"RSS 來源 SSL 驗證失敗，跳過 {url}：{e}")
        return []
    except Exception as e:
        logger.warning(f"RSS 抓取失敗 {url}: {e}")
        return []


def _get_entry_url(entry) -> Optional[str]:
    """取得新聞 URL"""
    url = entry.get("link") or entry.get("id") or ""
    url = url.strip()
    if url.startswith("http"):
        return url
    return None


def _extract_content(entry) -> str:
    """從 RSS entry 提取純文字內容"""
    # 嘗試多個欄位
    for field in ["content", "summary", "description"]:
        value = entry.get(field)
        if not value:
            continue
        # content 可能是列表
        if isinstance(value, list) and value:
            value = value[0].get("value", "")
        if value:
            # 移除 HTML 標籤（簡單版）
            import re
            text = re.sub(r"<[^>]+>", " ", str(value))
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 50:
                return text
    return entry.get("title", "")


def _parse_date(entry) -> Optional[datetime]:
    """解析發布日期"""
    for field in ["published_parsed", "updated_parsed"]:
        t = entry.get(field)
        if t:
            try:
                return datetime(*t[:6])
            except Exception:
                pass
    return None


def _clean_text(text: str) -> str:
    """清理標題文字"""
    import re
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _count_today_articles(db) -> int:
    """統計台灣今天（UTC+8）已收集的新聞數量"""
    from datetime import timedelta as _td
    tw_today = (datetime.utcnow() + _td(hours=8)).date()
    start_utc = datetime(tw_today.year, tw_today.month, tw_today.day) - _td(hours=8)
    end_utc   = start_utc + _td(days=1)
    return (
        db.query(NewsArticle)
        .filter(
            NewsArticle.collected_date >= start_utc,
            NewsArticle.collected_date <  end_utc,
        )
        .count()
    )


def _finish_log(log, db, status, collected, sources, start_time, error=None):
    """更新爬蟲執行紀錄"""
    log.status             = status
    log.articles_collected = collected
    log.sources_checked    = sources
    log.duration_seconds   = round(time.time() - start_time, 2)
    log.error_message      = error
    db.commit()


def reclassify_article(article_id: int) -> bool:
    """重新分類單篇新聞"""
    db = SessionLocal()
    try:
        article = db.query(NewsArticle).filter_by(id=article_id).first()
        if not article:
            return False

        result = classify_article(
            article.title,
            article.raw_content or article.title,
            article.source_name or ""
        )
        if result:
            article.attack_type     = result["attack_type"]
            article.region          = result["region"]
            article.affected_system = result["affected_system"]
            article.severity        = result["severity"]
            article.summary         = result["summary"]
            db.commit()
            return True
        return False
    finally:
        db.close()


def reclassify_all_articles(batch_delay: float = None) -> dict:
    """
    重新分類資料庫中所有新聞。
    batch_delay：每篇之間的等待秒數，None 則自動偵測（Groq=1s，Gemini=4s）
    回傳 {"total": N, "success": N, "failed": N}
    """
    from .config import get_llm_config

    if batch_delay is None:
        try:
            cfg = get_llm_config()
            batch_delay = 1.0 if cfg.get("provider", "groq") == "groq" else 4.0
        except Exception:
            batch_delay = 1.0

    db = SessionLocal()
    try:
        articles = db.query(NewsArticle).order_by(NewsArticle.id).all()
        total   = len(articles)
        success = 0
        failed  = 0

        logger.info(f"[重新分類] 共 {total} 篇，延遲 {batch_delay}s/篇")

        for i, article in enumerate(articles, 1):
            try:
                result = classify_article(
                    article.title,
                    article.raw_content or article.title,
                    article.source_name or ""
                )
                article.attack_type     = result["attack_type"]
                article.region          = result["region"]
                article.affected_system = result["affected_system"]
                article.severity        = result["severity"]
                article.summary         = result["summary"]
                db.commit()
                success += 1
                logger.info(
                    f"[重新分類] [{i}/{total}] ✅ id={article.id} "
                    f"{result['attack_type']}｜{result['region']}｜{result['severity']}"
                )
            except Exception as e:
                failed += 1
                logger.error(f"[重新分類] [{i}/{total}] ❌ id={article.id} 失敗：{e}")

            if i < total:
                time.sleep(batch_delay)

        logger.info(f"[重新分類] 完成：成功 {success}，失敗 {failed}")
        return {"total": total, "success": success, "failed": failed}
    finally:
        db.close()


def cleanup_old_articles(retention_days: int = 90) -> int:
    """清除超過保留期限的舊新聞，回傳刪除數量"""
    from datetime import timedelta
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        deleted = (
            db.query(NewsArticle)
            .filter(NewsArticle.collected_date < cutoff)
            .delete()
        )
        db.commit()
        return deleted
    finally:
        db.close()
