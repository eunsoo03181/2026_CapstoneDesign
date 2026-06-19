"""
로컬 로그인 라우터 — id / 비밀번호 가입·로그인.

엔드포인트:
  POST /auth/signup    : 회원가입
  POST /auth/login     : 로그인 (세션 쿠키 발급)
  POST /auth/logout    : 로그아웃 (= Google 로그아웃과 공유 — google_auth_routes 와 동일 path)
  POST /auth/change-password : 비밀번호 변경 (로그인 상태)
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from db import get_db, User, gen_uuid, EmailVerificationToken
from auth.password import hash_password, verify_password, needs_rehash
from auth.deps import get_current_user
from app.services.email_service import send_verification_email


VERIFICATION_TOKEN_TTL_HOURS = 24


router = APIRouter(prefix="/auth", tags=["auth-local"])


# ---------- 요청/응답 스키마 ----------

class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email:    EmailStr
    password: str = Field(min_length=8, max_length=128)
    name:     str = Field(default="", max_length=100)
    phone:    Optional[str] = Field(default=None, max_length=20)


class LoginRequest(BaseModel):
    # username 또는 email 둘 다 허용
    identifier: str
    password:   str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    id:             str
    email:          str
    name:           str
    username:       Optional[str]
    picture:        Optional[str]
    phone:          Optional[str]
    auth_provider:  str
    role:           str
    is_active:      bool


def _to_user_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id, email=u.email, name=u.name, username=u.username,
        picture=u.picture, phone=u.phone,
        auth_provider=u.auth_provider, role=u.role, is_active=u.is_active,
    )


# ---------- 라우트 ----------

def _issue_verification_token(db: Session, user: User, request: Request) -> str:
    """새 인증 토큰 발급 + DB 저장. URL 도 함께 반환 (메일 본문에 박을 용도)."""
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=VERIFICATION_TOKEN_TTL_HOURS)
    row = EmailVerificationToken(
        id=gen_uuid(),
        user_id=user.id,
        token=token,
        expires_at=expires,
    )
    db.add(row)
    db.commit()
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/verify-email?token={token}"


@router.post("/signup", response_model=UserResponse, status_code=201)
def signup(req: SignupRequest, request: Request, db: Session = Depends(get_db)):
    """로컬 계정 가입. 성공 시 자동 로그인 + 인증 메일 발송.

    가입은 됐지만 email_verified=False — 면접 시작은 인증 완료까지 차단됨.
    """
    # 중복 체크
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(409, "이미 사용 중인 아이디입니다.")
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(409, "이미 가입된 이메일입니다.")

    user = User(
        id=gen_uuid(),
        username=req.username,
        email=str(req.email),
        password_hash=hash_password(req.password),
        name=req.name or req.username,
        phone=req.phone,
        auth_provider="local",
        role="user",
        is_active=True,
        email_verified=False,    # 인증 메일 클릭 전까지 False
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 인증 메일 발송 — 실패해도 가입은 유지 (재발송 가능)
    try:
        verify_url = _issue_verification_token(db, user, request)
        send_verification_email(
            to_email=user.email,
            user_name=user.name or user.username or "회원",
            verify_url=verify_url,
        )
    except Exception:
        # 메일 발송은 옵션 — 실패해도 사용자에겐 재발송 안내
        pass

    # 자동 로그인 (인증 안 됐어도 페이지는 들어올 수 있음. 면접 시작만 차단)
    request.session["user_id"] = user.id
    user.last_login_at = datetime.utcnow()
    db.commit()

    return _to_user_response(user)


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    """이메일 인증 콜백 — 메일 본문 링크 클릭 시 진입.

    성공: /login?verified=1 로 리다이렉트
    실패: /login?verified=0&reason=... 로 리다이렉트
    """
    row = (
        db.query(EmailVerificationToken)
          .filter(EmailVerificationToken.token == token)
          .first()
    )
    if not row:
        return RedirectResponse(url="/login?verified=0&reason=invalid", status_code=302)
    if row.used_at is not None:
        return RedirectResponse(url="/login?verified=0&reason=already_used", status_code=302)
    if row.expires_at < datetime.utcnow():
        return RedirectResponse(url="/login?verified=0&reason=expired", status_code=302)

    user = db.get(User, row.user_id)
    if not user:
        return RedirectResponse(url="/login?verified=0&reason=user_gone", status_code=302)

    user.email_verified = True
    row.used_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/login?verified=1", status_code=302)


@router.post("/resend-verification")
def resend_verification(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """로그인된 사용자가 인증 메일 재발송 요청.

    이미 인증된 사용자: 400. 카오 OAuth 사용자: 400 (이미 검증된 이메일).
    """
    if current_user.email_verified:
        raise HTTPException(400, "이미 이메일 인증이 완료된 계정입니다.")
    if current_user.auth_provider != "local":
        raise HTTPException(400, "소셜 로그인 계정은 별도 인증이 필요하지 않습니다.")

    try:
        verify_url = _issue_verification_token(db, current_user, request)
        ok = send_verification_email(
            to_email=current_user.email,
            user_name=current_user.name or current_user.username or "회원",
            verify_url=verify_url,
        )
    except Exception as e:
        raise HTTPException(500, f"인증 메일 발송 실패: {e}")

    return {
        "ok": True,
        "sent": ok,
        "email": current_user.email,
        # SMTP 미설정이면 ok=True/sent=False — 콘솔에 링크 출력됨
        "fallback_console": (not ok),
    }


@router.post("/login", response_model=UserResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """username 또는 email + 비밀번호로 로그인."""
    user = (
        db.query(User)
        .filter(
            (User.username == req.identifier) | (User.email == req.identifier)
        )
        .first()
    )
    if not user or not user.password_hash:
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    if not user.is_active:
        raise HTTPException(403, "비활성화된 계정입니다.")

    # 해시 강도가 낮으면 이번 기회에 재해싱 (보안 점진 강화)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(req.password)

    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)

    request.session["user_id"] = user.id
    return _to_user_response(user)


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """로그인 상태에서 비밀번호 변경 (로컬 계정만)."""
    if current_user.auth_provider != "local" or not current_user.password_hash:
        raise HTTPException(
            400, "소셜 로그인 계정은 비밀번호 변경을 지원하지 않습니다.",
        )
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(401, "현재 비밀번호가 올바르지 않습니다.")

    current_user.password_hash = hash_password(req.new_password)
    db.commit()
    return {"ok": True}
