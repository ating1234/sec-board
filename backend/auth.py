"""
管理介面身份驗證
- 密碼以 PBKDF2-SHA256 雜湊儲存於資料庫
- Session token 存於 DB（重啟服務後仍有效）
- Session 有效期 24 小時，過期自動清除
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional


SESSION_TTL = timedelta(hours=24)


# ── 密碼雜湊 ──────────────────────────────────────────────────

def hash_password(password: str, salt: Optional[str] = None) -> str:
    """回傳 'salt:hex_hash'，salt 若未給則自動產生"""
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=200_000,
    )
    return f"{salt}:{key.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """驗證密碼是否符合儲存的雜湊"""
    try:
        salt, _ = stored_hash.split(":", 1)
        expected = hash_password(password, salt)
        return hmac.compare_digest(stored_hash, expected)
    except Exception:
        return False


# ── Session 管理（DB 版）─────────────────────────────────────

def create_session() -> str:
    """建立新 Session，寫入 DB，回傳 token"""
    from .database import SessionLocal, AdminSession

    token      = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + SESSION_TTL

    db = SessionLocal()
    try:
        # 順便清除過期 sessions
        db.query(AdminSession).filter(
            AdminSession.expires_at < datetime.utcnow()
        ).delete()
        db.add(AdminSession(token=token, expires_at=expires_at))
        db.commit()
    finally:
        db.close()

    return token


def validate_session(token: Optional[str]) -> bool:
    """驗證 Session token 是否有效"""
    if not token:
        return False

    from .database import SessionLocal, AdminSession

    db = SessionLocal()
    try:
        row = db.query(AdminSession).filter_by(token=token).first()
        if row is None:
            return False
        if datetime.utcnow() > row.expires_at:
            db.delete(row)
            db.commit()
            return False
        return True
    finally:
        db.close()


def delete_session(token: Optional[str]) -> None:
    """登出：從 DB 刪除 Session"""
    if not token:
        return

    from .database import SessionLocal, AdminSession

    db = SessionLocal()
    try:
        db.query(AdminSession).filter_by(token=token).delete()
        db.commit()
    finally:
        db.close()


# ── 初始密碼初始化 ─────────────────────────────────────────────
#
# 不再使用硬編預設密碼。首次啟動時需從環境變數 INITIAL_ADMIN_PASSWORD 讀取，
# 未設定即拒絕啟動，避免 Dashboard 被 "admin" / "admin" 爆破。
# 使用方式（.env 或部署平台環境變數）：
#     INITIAL_ADMIN_PASSWORD=<至少 8 碼的強密碼>
# 初始化完成後可安全移除此環境變數，並登入後從「一般設定」更改密碼。


class AdminPasswordNotConfigured(RuntimeError):
    """首次啟動但未設定 INITIAL_ADMIN_PASSWORD 時拋出。"""


def get_or_create_password_hash() -> tuple[str, bool]:
    """
    從資料庫取得密碼雜湊，若尚未設定則從環境變數 INITIAL_ADMIN_PASSWORD 建立。
    回傳 (hash, is_new)。若首次啟動且未提供該環境變數，會 raise AdminPasswordNotConfigured。
    """
    from .config import get_setting, set_setting
    stored = get_setting("admin_password_hash", "")
    if stored:
        return stored, False

    initial_pw = os.environ.get("INITIAL_ADMIN_PASSWORD", "").strip()
    if not initial_pw:
        raise AdminPasswordNotConfigured(
            "尚未設定管理員密碼。請在環境變數設定 INITIAL_ADMIN_PASSWORD=<至少 8 碼>，"
            "初始化完成後可移除此環境變數。"
        )
    if len(initial_pw) < 8:
        raise AdminPasswordNotConfigured(
            "INITIAL_ADMIN_PASSWORD 長度需至少 8 碼，請設定更強的密碼。"
        )
    new_hash = hash_password(initial_pw)
    set_setting("admin_password_hash", new_hash)
    return new_hash, True
