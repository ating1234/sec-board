"""
管理介面身份驗證
- 密碼以 PBKDF2-SHA256 雜湊儲存於資料庫
- Session token 存於 DB（重啟服務後仍有效）
- Session 有效期 24 小時，過期自動清除
"""

import hashlib
import hmac
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


# ── 預設密碼初始化 ─────────────────────────────────────────────

DEFAULT_PASSWORD = "admin"


def get_or_create_password_hash() -> tuple[str, bool]:
    """
    從資料庫取得密碼雜湊，若尚未設定則建立預設密碼並存入資料庫。
    回傳 (hash, is_new)
    """
    from .config import get_setting, set_setting
    stored = get_setting("admin_password_hash", "")
    if not stored:
        new_hash = hash_password(DEFAULT_PASSWORD)
        set_setting("admin_password_hash", new_hash)
        return new_hash, True
    return stored, False
