"""
Google OAuth 2.0 / OpenID Connect — authlib 기반.

[설정 절차]
1. https://console.cloud.google.com 접속
2. 프로젝트 생성 → API 및 서비스 → 사용자 인증 정보 → OAuth 클라이언트 ID 생성
3. 애플리케이션 유형: 웹 애플리케이션
4. 승인된 리디렉션 URI:
   - 개발: http://localhost:8000/auth/google/callback
   - 배포: https://YOUR-DOMAIN/auth/google/callback
5. 발급된 Client ID / Secret 을 환경변수로:
   export GOOGLE_CLIENT_ID="..."
   export GOOGLE_CLIENT_SECRET="..."
   export SESSION_SECRET_KEY="아무거나 긴 랜덤 문자열"   # FastAPI Session 미들웨어용
"""

import os
from typing import Optional

from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session

from db import User
from db.utils import gen_uuid


oauth = OAuth()


def register_google_oauth() -> None:
    """앱 시작 시 1회 호출 — OAuth 클라이언트 등록."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        # 환경변수 미설정 시에도 앱은 로드되어야 함. 실제 OAuth 진입에서만 에러.
        return

    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=(
            "https://accounts.google.com/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
    )


def get_or_create_user(db: Session, google_userinfo: dict) -> User:
    """
    Google OAuth 콜백에서 받은 user info(dict)로 User row 를 upsert.

    google_userinfo 예:
      {
        "sub": "1234567890",
        "email": "user@gmail.com",
        "name": "홍길동",
        "picture": "https://...",
        ...
      }
    """
    sub = google_userinfo["sub"]
    user = db.query(User).filter(User.google_sub == sub).first()
    if user:
        # 프로필 변경 사항 동기화
        user.email = google_userinfo.get("email", user.email)
        user.name = google_userinfo.get("name", user.name)
        user.picture = google_userinfo.get("picture", user.picture)
        # Google 로그인 시점에 이메일 인증 강제 활성화 (이미 검증된 이메일)
        user.email_verified = True
        db.commit()
        db.refresh(user)
        return user

    user = User(
        id=gen_uuid(),
        google_sub=sub,
        email=google_userinfo.get("email", ""),
        name=google_userinfo.get("name", ""),
        picture=google_userinfo.get("picture"),
        auth_provider="google",
        # Google 이 이미 이메일을 검증한 사용자 — 별도 인증 메일 불필요
        email_verified=bool(google_userinfo.get("email_verified", True)),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
