"""
비밀번호 해싱 유틸 — bcrypt.

평문 비밀번호는 절대 DB에 저장하지 않는다.
사용 예:
    from auth.password import hash_password, verify_password
    h = hash_password("user_password_plain")
    ok = verify_password("user_password_plain", h)   # True
"""

import bcrypt


def hash_password(plain: str) -> str:
    """bcrypt 해시 생성. salt 자동 포함, 결과는 약 60자."""
    if not isinstance(plain, str) or not plain:
        raise ValueError("비밀번호는 비어있지 않은 문자열이어야 합니다.")
    salt = bcrypt.gensalt(rounds=12)   # 12 라운드 = 일반적인 안전 수준
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """평문이 해시와 일치하는지 검증. 잘못된 해시 형식이면 False 반환."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str, min_rounds: int = 12) -> bool:
    """저장된 해시가 현재 권장 강도보다 약하면 True. 로그인 성공 후 재해싱 트리거용."""
    try:
        # bcrypt 해시 형식: $2b$<rounds>$<salt><hash>
        parts = hashed.split("$")
        if len(parts) < 4:
            return True
        return int(parts[2]) < min_rounds
    except (ValueError, IndexError):
        return True
