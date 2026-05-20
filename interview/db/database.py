"""
DB 엔진 / 세션 팩토리.

환경변수:
  DATABASE_URL   - 기본값: sqlite:///mocktalk.db
                   예) postgresql+psycopg://user:pass@host:6543/postgres
                       (Supabase Transaction Pooler 권장 포트 6543)
"""

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE = f"sqlite:///{BASE_DIR / 'mocktalk.db'}"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE)


# ============================================================
# 드라이버별 연결 옵션
# ============================================================
_connect_args: dict = {}
_engine_kwargs: dict = {"echo": False}

if DATABASE_URL.startswith("sqlite"):
    # SQLite — multithread 허용
    _connect_args["check_same_thread"] = False
else:
    # PostgreSQL (Supabase 등)
    # Supabase Transaction Pooler (port 6543) 는 PgBouncer/Supavisor 의 transaction mode 로
    # connection 을 클라이언트 간 재사용한다.
    # psycopg(v3) 기본 동작은 server-side prepared statement 를 자동 생성하므로
    # 같은 connection 에 다른 클라이언트가 들어오면 "_pg3_0 already exists" 충돌 발생.
    # → prepare_threshold=None 으로 prepared statement 자동 생성을 끔.
    if "+psycopg" in DATABASE_URL or DATABASE_URL.startswith("postgresql"):
        _connect_args["prepare_threshold"] = None

    # 풀러가 idle connection 을 끊을 수 있으므로 pre_ping + recycle 권장
    _engine_kwargs.update({
        "pool_pre_ping": True,
        "pool_recycle": 300,   # 5분
    })


engine = create_engine(DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI Depends 용 — 요청마다 세션 1개 생성·종료."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """
    개발용 — 모델에 정의된 모든 테이블을 생성.
    프로덕션에선 Alembic 마이그레이션 사용 권장.
    """
    from .models import Base  # 순환 import 회피
    Base.metadata.create_all(bind=engine)


def drop_all() -> None:
    """⚠ 모든 테이블 삭제 — 개발/테스트 전용."""
    from .models import Base
    Base.metadata.drop_all(bind=engine)
