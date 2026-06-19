"""Google OAuth 라우터 — /auth/google/login, /auth/google/callback, /auth/logout, /auth/me."""

import os
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from db import get_db, User
from auth.google import oauth, get_or_create_user
from auth.deps import get_current_user


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
async def google_login(request: Request):
    """구글 로그인 페이지로 리다이렉트."""
    if "google" not in oauth._clients:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 환경변수가 설정되지 않았습니다.",
        )
    redirect_uri = request.url_for("google_callback")
    # 디버그 로그 — uvicorn 콘솔에 정확한 URI 출력.
    # Google Console 의 '승인된 리디렉션 URI' 에 이 값을 그대로 등록해야 함.
    print(f"[OAuth] Google authorize_redirect → redirect_uri={redirect_uri!s}")
    return await oauth.google.authorize_redirect(request, str(redirect_uri))


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    """구글 OAuth 콜백 — 토큰 받아서 user upsert + 세션 저장."""
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth 실패: {e}")

    userinfo = token.get("userinfo")
    if not userinfo:
        # OIDC userinfo 가 없으면 ID token 에서 추출
        userinfo = await oauth.google.parse_id_token(request, token)

    user = get_or_create_user(db, dict(userinfo))
    request.session["user_id"] = user.id
    # 로그인 후 메인 페이지로
    return RedirectResponse(url="/")


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
async def me(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    현재 로그인된 사용자 정보.
    임퍼소네이션 중이면 원래 관리자 정보를 함께 반환 → 프론트 배너 표시용.
    """
    admin_id = request.session.get("admin_id")
    impersonating = bool(admin_id) and admin_id != current_user.id
    admin_info = None
    if impersonating:
        a = db.query(User).filter(User.id == admin_id).first()
        if a:
            admin_info = {"id": a.id, "name": a.name, "email": a.email}

    is_unlimited = current_user.role in ("admin", "moderator")
    return {
        "id":      current_user.id,
        "email":   current_user.email,
        "name":    current_user.name,
        "picture": current_user.picture,
        "role":    current_user.role,
        "is_active": current_user.is_active,
        "impersonating": impersonating,
        "admin": admin_info,
        # Credit 잔액 — 헤더 chip 에 표시. admin/moderator 는 무제한.
        "credits": int(current_user.credits or 0),
        "credits_unlimited": is_unlimited,
        # 이메일 인증 — False 면 면접 시작 차단됨. 헤더에서 재발송 안내.
        "email_verified": bool(getattr(current_user, "email_verified", True)),
        "auth_provider": current_user.auth_provider,
    }
