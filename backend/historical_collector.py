"""
歷史數據收集器（一次性使用）
收集所有啟用來源近 N 天的新聞，無每日數量上限
Gemini 免費方案限速：15 RPM → 每次呼叫後等待 4 秒
"""

import logging
import time
import sys
from datetime import datetime, timedelta
from typing import List, Optional

import feedparser
import requests

from .database import SessionLocal, NewsArticle, NewsSource, CrawlerLog
from .classifier import classify_article
from .collector import _fetch_feed, _get_entry_url, _extract_content, _parse_date, _clean_text

logger = logging.getLogger(__name__)

# Groq 免費方案：30 RPM → 每次請求約 2 秒
# Gemini 免費方案：15 RPM → 每次請求約 4 秒
# 根據設定自動選擇延遲
def _get_llm_delay() -> float:
    try:
        from .config import get_llm_config
        cfg = get_llm_config()
        return 2.0 if cfg.get("provider", "groq") == "groq" else 4.0
    except Exception:
        return 2.0

GEMINI_DELAY = 4.0  # 向後相容（未使用）


def run_historical_collection(days: int = 30, verbose: bool = True) -> dict:
    """
    收集所有啟用來源近 days 天的歷史新聞
    不受每日數量上限限制，適合一次性初始化資料
    """
    start_time = time.time()
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    db = SessionLocal()
    log = CrawlerLog(run_at=datetime.utcnow(), status="running")
    db.add(log)
    db.commit()
    db.refresh(log)

    if verbose:
        _print_banner(days, cutoff_date)

    try:
        sources = (
            db.query(NewsSource)
            .filter(NewsSource.enabled == True)
            .order_by(NewsSource.priority.asc(), NewsSource.name.asc())
            .all()
        )

        if verbose:
            print(f"\n📡 找到 {len(sources)} 個啟用來源\n")

        total_collected  = 0
        total_skipped    = 0   # 已存在（重複）
        total_outdated   = 0   # 超出時間範圍
        sources_checked  = 0

        for src_idx, source in enumerate(sources, 1):
            if verbose:
                print(f"[{src_idx}/{len(sources)}] 📰 {source.name}")
                print(f"    URL：{source.url}")

            entries = _fetch_feed(source.url)
            if not entries:
                if verbose:
                    print("    ⚠️  無法取得 RSS，跳過\n")
                continue

            sources_checked += 1
            src_collected = 0

            for entry in entries:
                url = _get_entry_url(entry)
                if not url:
                    continue

                # 解析發布日期
                pub_date = _parse_date(entry)

                # 過濾超出時間範圍的文章
                if pub_date and pub_date < cutoff_date:
                    total_outdated += 1
                    continue

                # 跳過已存在的
                if db.query(NewsArticle).filter_by(url=url).first():
                    total_skipped += 1
                    if verbose:
                        sys.stdout.write("  → [已存在，跳過]\n")
                    continue

                title   = _clean_text(entry.get("title", "（無標題）"))
                content = _extract_content(entry)

                if verbose:
                    print(f"  → 分類中：{title[:65]}")

                # 呼叫 Gemini 分類
                classification = classify_article(title, content, source.name)

                article = NewsArticle(
                    title           = title,
                    url             = url,
                    source_name     = source.name,
                    published_date  = pub_date,
                    collected_date  = datetime.utcnow(),
                    raw_content     = content[:5000],
                    attack_type     = classification.get("attack_type"),
                    region          = classification.get("region"),
                    affected_system = classification.get("affected_system"),
                    severity        = classification.get("severity"),
                    summary         = classification.get("summary"),
                )
                db.add(article)
                db.commit()

                total_collected += 1
                src_collected   += 1

                if verbose:
                    sev = classification.get("severity", "?")
                    atk = classification.get("attack_type", "?")
                    reg = classification.get("region", "?")
                    print(f"     ✅ 已儲存｜{sev}｜{atk}｜{reg}")

                # LLM 速率限制（Groq: 2s，Gemini: 4s）
                time.sleep(_get_llm_delay())

            if verbose:
                print(f"  📊 本來源收集：{src_collected} 篇\n")

        elapsed = time.time() - start_time
        _update_log(log, db, "success", total_collected, sources_checked, elapsed)

        result = {
            "status":          "success",
            "collected":       total_collected,
            "skipped":         total_skipped,
            "outdated":        total_outdated,
            "sources_checked": sources_checked,
            "elapsed_seconds": round(elapsed, 1),
        }

        if verbose:
            _print_summary(result)

        return result

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        _update_log(log, db, "partial", total_collected, sources_checked, elapsed, "使用者中止")
        if verbose:
            print("\n\n⚠️  使用者中止收集")
            print(f"已收集：{total_collected} 篇")
        return {"status": "partial", "collected": total_collected}

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"歷史收集失敗：{e}", exc_info=True)
        _update_log(log, db, "failed", 0, 0, elapsed, str(e))
        if verbose:
            print(f"\n❌ 發生錯誤：{e}")
        return {"status": "failed", "error": str(e)}

    finally:
        db.close()


def _update_log(log, db, status, collected, sources, elapsed, error=None):
    log.status              = status
    log.articles_collected  = collected
    log.sources_checked     = sources
    log.duration_seconds    = round(elapsed, 2)
    log.error_message       = error
    db.commit()


def _print_banner(days, cutoff_date):
    print("=" * 60)
    print("🛡️  資安新聞分析平台 — 歷史數據收集")
    print("=" * 60)
    print(f"📅 收集範圍：近 {days} 天")
    print(f"   起始日期：{cutoff_date.strftime('%Y-%m-%d')} 至今")
    delay = _get_llm_delay()
    print(f"⏱️  預估時間：依文章數量而定（每篇約 {delay} 秒）")
    print(f"   （按 Ctrl+C 可隨時中止，已收集的會保留）")
    print("=" * 60)


def _print_summary(result):
    elapsed = result['elapsed_seconds']
    mins    = int(elapsed // 60)
    secs    = int(elapsed % 60)

    print("=" * 60)
    print("✅ 歷史數據收集完成！")
    print(f"   新增新聞：{result['collected']} 篇")
    print(f"   略過重複：{result['skipped']} 篇")
    print(f"   超出範圍：{result['outdated']} 篇")
    print(f"   來源數量：{result['sources_checked']} 個")
    print(f"   總耗時  ：{mins} 分 {secs} 秒")
    print("=" * 60)
    print("現在可以啟動服務查看結果：bash start_macos.sh")
