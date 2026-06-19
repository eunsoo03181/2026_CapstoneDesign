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
from sqlalchemy.pool import NullPool


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE = f"sqlite:///{BASE_DIR / 'mocktalk.db'}"

# 빈 문자열("DATABASE_URL=" 만 있는 경우)도 fallback 으로 처리 — os.getenv 의 default 인자는
# key 가 아예 없을 때만 동작하므로 명시적으로 truthy 체크.
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip() or DEFAULT_SQLITE


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
        # 짧은 connect timeout — 네트워크 끊김 시 빠른 실패
        _connect_args["connect_timeout"] = 10
        # TCP keepalive (PostgreSQL 표준 옵션 via libpq 스타일 인자)
        # ZeroTier·NAT 환경에서 idle connection 이 조용히 죽는 걸 방지
        _connect_args["keepalives"]         = 1
        _connect_args["keepalives_idle"]    = 30
        _connect_args["keepalives_interval"] = 10
        _connect_args["keepalives_count"]   = 5

    # ⚠️ Supabase Pooler 가 이미 server-side connection pooling 을 하므로
    # SQLAlchemy 측에서 또 풀링하면 죽은 connection 을 캐싱하다 위 에러 발생.
    # NullPool — 매 요청마다 새 connection. pooler 가 이미 효율적으로 재사용해줌.
    _engine_kwargs.update({
        "poolclass": NullPool,
        "pool_pre_ping": True,   # NullPool 에서도 안전 차원으로 유지
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
    개발용 — 모델에 정의된 모든 테이블을 생성 + 가벼운 컬럼 마이그레이션.
    프로덕션에선 Alembic 마이그레이션 사용 권장.
    """
    from .models import Base  # 순환 import 회피
    Base.metadata.create_all(bind=engine)
    _auto_add_columns_sqlite()


def _auto_add_columns_sqlite() -> None:
    """SQLite 한정 — 기존 테이블에 신규 컬럼이 없으면 ALTER TABLE 로 자동 추가.

    Alembic 도입 전까지의 임시 호환 레이어.
    Postgres 환경에서는 no-op (콘솔에서 ALTER 실행 필요).
    """
    if not str(engine.url).startswith("sqlite"):
        return
    from sqlalchemy import text
    # 기존 DB 에 빠질 가능성 있는 신규 컬럼 목록
    # 형식: (table, column, sql_type_with_default)
    desired = [
        ("users", "credits", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "suspended_until", "DATETIME"),
        # 이메일 인증 컬럼 — 기존 사용자는 grandfather (아래 UPDATE 로 True 처리)
        ("users", "email_verified", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    with engine.begin() as conn:
        for table, col, decl in desired:
            try:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                existing = {r[1] for r in rows}  # 두 번째 컬럼이 이름
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))
                    # email_verified 신규 추가 시 — 기존 사용자 모두 grandfather True 처리
                    if table == "users" and col == "email_verified":
                        conn.execute(text("UPDATE users SET email_verified = 1"))
            except Exception:
                # 테이블 자체가 없으면 create_all 이 만들었을 것 — 무시
                pass



def drop_all() -> None:
    """⚠ 모든 테이블 삭제 — 개발/테스트 전용."""
    from .models import Base
    Base.metadata.drop_all(bind=engine)
