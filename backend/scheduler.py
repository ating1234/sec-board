"""
APScheduler 排程管理
支援一天多次執行（以逗號分隔的小時列表）
動態更新排程時間（管理介面修改後立即生效）
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import get_crawler_config
from .collector import run_crawler, cleanup_old_articles

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Taipei")
CRAWLER_JOB_ID = "daily_crawler"


def start_scheduler():
    """啟動排程器，載入當前設定"""
    cfg = get_crawler_config()
    _add_or_replace_job(cfg["schedule_hours"], cfg["schedule_minute"])

    if not scheduler.running:
        scheduler.start()
        logger.info(
            f"排程器已啟動，每日 {cfg['schedule_hours']} 時 {cfg['schedule_minute']:02d} 分執行"
        )


def stop_scheduler():
    """停止排程器"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("排程器已停止")


def update_schedule(hours_str: str, minute: int):
    """
    動態更新排程時間（管理介面呼叫）
    hours_str: 逗號分隔小時，例如 "8,14,20"
    不需要重啟應用程式
    """
    _add_or_replace_job(hours_str, minute)
    logger.info(f"排程已更新：每日 {hours_str} 時 {minute:02d} 分執行")


def _add_or_replace_job(hours_str: str, minute: int):
    """新增或替換爬蟲排程任務（支援多小時）"""
    if scheduler.get_job(CRAWLER_JOB_ID):
        scheduler.remove_job(CRAWLER_JOB_ID)

    # APScheduler CronTrigger 原生支援逗號分隔小時，如 "8,14,20"
    hours_clean = ",".join(h.strip() for h in str(hours_str).split(",") if h.strip())

    scheduler.add_job(
        func=_crawler_with_cleanup,
        trigger=CronTrigger(hour=hours_clean, minute=minute, timezone="Asia/Taipei"),
        id=CRAWLER_JOB_ID,
        name="資安新聞爬蟲",
        replace_existing=True,
        misfire_grace_time=3600,
    )


def _crawler_with_cleanup():
    """執行爬蟲 + 清理舊資料（排程觸發用）"""
    logger.info("排程觸發：開始執行爬蟲")
    result = run_crawler()
    logger.info(f"爬蟲結果：{result}")

    cfg = get_crawler_config()
    deleted = cleanup_old_articles(cfg["retention_days"])
    if deleted > 0:
        logger.info(f"清理舊新聞：刪除 {deleted} 篇")


def get_next_run_time() -> str:
    """取得下次執行時間（字串）"""
    job = scheduler.get_job(CRAWLER_JOB_ID)
    if job and job.next_run_time:
        return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    return "未排程"


def get_schedule_summary() -> str:
    """回傳目前排程摘要，例如 '每日 08:00、14:00、20:00 執行'"""
    cfg = get_crawler_config()
    hours = [h.strip() for h in str(cfg["schedule_hours"]).split(",") if h.strip()]
    minute = cfg["schedule_minute"]
    times = "、".join(f"{int(h):02d}:{minute:02d}" for h in hours)
    return f"每日 {times} 執行"
