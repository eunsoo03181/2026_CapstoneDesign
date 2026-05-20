"""DB 식별자 생성 유틸."""

import uuid
import secrets
import string

# 가독성 좋은 base62 (대소문자 + 숫자, ambiguous 문자 그대로 포함)
_PUBLIC_ALPHABET = string.ascii_letters + string.digits


def gen_uuid() -> str:
    """내부 PK용 — UUID4 hex (32자)."""
    return uuid.uuid4().hex


def gen_public_code(length: int = 10) -> str:
    """
    공개 식별자 — 외부 URL/공유에 노출되는 짧은 코드.
    62^10 ≈ 8.4×10^17 가지 → 사실상 충돌 없음.

    예: 'Kj9mPx2Rqs'
    """
    return "".join(secrets.choice(_PUBLIC_ALPHABET) for _ in range(length))


def gen_share_token(length: int = 24) -> str:
    """
    선택적 공유 토큰 — share_token. 폐기/재발급 가능한 비밀값.
    URL-safe Base64 (62 문자) 길이 24 ≈ 142bit entropy.
    """
    return secrets.token_urlsafe(length)[:length]
