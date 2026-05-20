"""
로컬 로그인 라우터 — id / 비밀번호 가입·로그인.

엔드포인트:
  POST /auth/signup    : 회원가입
  POST /auth/login     : 로그인 (세션 쿠키 발급)
  POST /auth/logout    : 로그아웃 (= Google 로그아웃과 공유 — google_auth_routes 와 동일 path)
  POST /auth/change-password : 비밀번호 변경 (로그인 상태)
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from db import get_db, User, gen_uuid
from auth.password import hash_password, verify_password, needs_rehash
from auth.deps import get_current_user


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

@router.post("/signup", response_model=UserResponse, status_code=201)
def signup(req: SignupRequest, request: Request, db: Session = Depends(get_db)):
    """로컬 계정 가입. 성공 시 자동 로그인 (세션 쿠키 발급)."""
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
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 자동 로그인
    request.session["user_id"] = user.id
    user.last_login_at = datetime.utcnow()
    db.commit()

    return _to_user_response(user)


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
