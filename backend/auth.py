"""
管理介面身份驗證
- 密碼以 PBKDF2-SHA256 雜湊儲存於資料庫
- 登入後發給隨機 Session Token（Cookie）
- Session 有效期 24 小時
"""

import hashlib
import hmac
import secrets
import time
from typing import Optional


# ── Session 記憶體儲存 ─────────────────────────────────────────
# {token: expiry_timestamp}
_sessions: dict[str, float] = {}
SESSION_TTL = 24 * 3600  # 24 小時


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


# ── Session 管理 ───────────────────────────────────────────────

def create_session() -> str:
    """建立新 Session，回傳 token"""
    # 清除過期 sessions
    now = time.time()
    expired = [t for t, exp in _sessions.items() if exp < now]
    for t in expired:
        _sessions.pop(t, None)

    token = secrets.token_urlsafe(32)
    _sessions[token] = now + SESSION_TTL
    return token


def validate_session(token: Optional[str]) -> bool:
    """驗證 Session token 是否有效"""
    if not token:
        return False
    exp = _sessions.get(token)
    if exp is None:
        return False
    if time.time() > exp:
        _sessions.pop(token, None)
        return False
    return True


def delete_session(token: Optional[str]) -> None:
    """登出：刪除 Session"""
    if token:
        _sessions.pop(token, None)


# ── 預設密碼初始化 ─────────────────────────────────────────────

DEFAULT_PASSWORD = "admin"


def get_or_create_password_hash() -> str:
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
