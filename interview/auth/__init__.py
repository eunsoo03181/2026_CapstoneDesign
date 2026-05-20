"""인증 모듈 — 로컬 로그인 + Google OAuth + 권한 체크."""

from .google import oauth, register_google_oauth, get_or_create_user
from .deps import (
    get_current_user,
    get_current_user_optional,
    require_admin,
    require_moderator,
    require_role,
)
from .password import hash_password, verify_password, needs_rehash

__all__ = [
    "oauth", "register_google_oauth", "get_or_create_user",
    "get_current_user", "get_current_user_optional",
    "require_admin", "require_moderator", "require_role",
    "hash_password", "verify_password", "needs_rehash",
]
