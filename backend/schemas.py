"""
Pydantic 資料驗證模型（API 輸入輸出格式）
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


# ──────────────────────────────────────────────
# 新聞
# ──────────────────────────────────────────────

class NewsArticleOut(BaseModel):
    id:              int
    title:           str
    summary:         Optional[str]
    url:             str
    source_name:     Optional[str]
    published_date:  Optional[datetime]
    collected_date:  Optional[datetime]
    attack_type:     Optional[str]
    region:          Optional[str]
    affected_system: Optional[str]
    severity:        Optional[str]

    class Config:
        from_attributes = True


class NewsListResponse(BaseModel):
    total:   int
    page:    int
    size:    int
    items:   List[NewsArticleOut]


# ──────────────────────────────────────────────
# 統計
# ──────────────────────────────────────────────

class StatsItem(BaseModel):
    label: str
    count: int


class DashboardStats(BaseModel):
    total_articles:       int
    today_articles:       int
    critical_articles:    int
    today_critical:       int
    month_critical:       int
    attack_types:         List[StatsItem]
    regions:              List[StatsItem]
    affected_systems:     List[StatsItem]
    severity_dist:        List[StatsItem]
    weekly_trend:         List[StatsItem]   # label = 日期字串


# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────

class SettingOut(BaseModel):
    key:         str
    value:       str
    description: Optional[str]
    updated_at:  Optional[datetime]
    has_value:   bool = False   # 敏感欄位遮罩時，仍讓前端知道「已設定」

    class Config:
        from_attributes = True


class SettingUpdate(BaseModel):
    value: str


class BulkSettingUpdate(BaseModel):
    settings: dict   # {key: value}


# ──────────────────────────────────────────────
# 新聞來源
# ──────────────────────────────────────────────

class NewsSourceOut(BaseModel):
    id:         int
    name:       str
    url:        str
    region:     Optional[str]
    enabled:    bool
    priority:   int
    created_at: Optional[datetime]

    class Config:
        from_attributes = True


class NewsSourceCreate(BaseModel):
    name:     str
    url:      str
    region:   Optional[str] = "全球"
    priority: Optional[int] = 3


class NewsSourceUpdate(BaseModel):
    name:     Optional[str]
    enabled:  Optional[bool]
    priority: Optional[int]
    region:   Optional[str]


# ──────────────────────────────────────────────
# 爬蟲紀錄
# ──────────────────────────────────────────────

class CrawlerLogOut(BaseModel):
    id:                 int
    run_at:             Optional[datetime]
    status:             Optional[str]
    articles_collected: Optional[int]
    sources_checked:    Optional[int]
    error_message:      Optional[str]
    duration_seconds:   Optional[float]

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────
# 管理 API 回應
# ──────────────────────────────────────────────

class SystemStats(BaseModel):
    total_articles:   int
    sources_enabled:  int
    sources_total:    int
    last_crawl:       Optional[datetime]
    last_crawl_status:Optional[str]
    db_size_estimate: str
