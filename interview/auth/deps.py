"""
FastAPI Depends — 현재 로그인된 사용자 추출.

세션(starlette SessionMiddleware) 기반:
  - 로그인 성공 후 request.session["user_id"] = user.id 로 저장
  - 이후 요청은 세션 쿠키에서 user_id 를 꺼내 DB 조회
"""

from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from db import User, get_db


def _maybe_lift_suspension(user: User, db: Session) -> None:
    """기간 정지가 만료됐으면 자동으로 해제.

    is_active=False + suspended_until 이 과거 → 활성화.
    영구 정지(suspended_until=None) 는 만료 개념이 없으므로 그대로 둠.
    """
    if user.is_active:
        return
    if user.suspended_until and user.suspended_until <= datetime.utcnow():
        user.is_active = True
        user.suspended_until = None
        db.commit()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """로그인 필수 — 미인증이거나 비활성 계정이면 401/403."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 세션입니다.",
        )
    _maybe_lift_suspension(user, db)
    if not user.is_active:
        request.session.clear()
        # 만료가 정해진 기간 정지면 안내문에 시각을 포함
        if user.suspended_until:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{user.suspended_until.strftime('%Y-%m-%d %H:%M')} 까지 정지된 계정입니다.",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="정지된 계정입니다.",
        )
    return user


def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """로그인 선택 — 미인증이면 None 반환 (공유 페이지 등에 사용)."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    _maybe_lift_suspension(user, db)
    if not user.is_active:
        return None
    return user


# ============================================================
# 권한 등급별 의존성
# 사용 예:
#   @app.get("/admin/users")
#   def list_users(user: User = Depends(require_admin)):
#       ...
# ============================================================

def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """admin 만 통과."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다.",
        )
    return current_user


def require_moderator(
    current_user: User = Depends(get_current_user),
) -> User:
    """moderator 또는 admin 통과."""
    if current_user.role not in ("admin", "moderator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="모더레이터 이상 권한이 필요합니다.",
        )
    return current_user


def require_role(*allowed_roles: str):
    """
    임의의 role 조합을 허용하는 Depends 팩토리.

    사용 예:
        @app.get("/foo", dependencies=[Depends(require_role("admin", "moderator"))])
        def foo(): ...
    """
    def _check(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"이 기능은 다음 권한만 사용 가능합니다: {allowed_roles}",
            )
        return current_user
    return _check
