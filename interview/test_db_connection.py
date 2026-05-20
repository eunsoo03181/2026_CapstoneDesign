"""
Supabase 연결 + SQLAlchemy ORM round-trip 테스트.

실행:
    source venv/bin/activate
    export DATABASE_URL="postgresql+psycopg://postgres.[ref]:[PW]@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
    python test_db_connection.py
"""

import os
import sys

# 1) 환경변수 확인
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("ERROR: DATABASE_URL 환경변수가 설정되지 않았습니다.")
    print("export DATABASE_URL='postgresql+psycopg://...' 후 다시 실행하세요.")
    sys.exit(1)

# psycopg v3 사용 확인
if db_url.startswith("postgresql://"):
    print("⚠ DATABASE_URL 이 'postgresql://' 로 시작합니다.")
    print("   SQLAlchemy 가 psycopg(v3)를 쓰도록 'postgresql+psycopg://' 로 바꾸세요.")
    print("   예: postgresql+psycopg://postgres.xxxx:PASSWORD@...:6543/postgres")
    sys.exit(1)

print(f"DATABASE_URL: {db_url[:40]}...{db_url[-30:]}")
print()

# 2) 원시 SQL 로 테이블 목록 확인
from sqlalchemy import create_engine, text
engine = create_engine(db_url)

print("=== [Test 1] 원시 SQL — public 스키마 테이블 목록 ===")
with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name"
    )).fetchall()
    for r in rows:
        print("  -", r[0])

expected = {"answers", "evaluations", "interview_sessions", "nonverbal_metrics",
            "questions", "resumes", "users", "video_clips"}
got = {r[0] for r in rows}
missing = expected - got
if missing:
    print(f"\n⚠ 다음 테이블이 없습니다: {missing}")
    print("   db/supabase_schema_no_auth.sql 를 SQL Editor 에서 실행했는지 확인하세요.")
    sys.exit(1)
print("✓ 8개 테이블 모두 존재")
print()

# 3) SQLAlchemy ORM round-trip
print("=== [Test 2] SQLAlchemy ORM 으로 더미 row 삽입/조회 ===")
from db import SessionLocal, User, InterviewSession, gen_uuid

db = SessionLocal()
try:
    test_email = "_connection_test@local.dev"

    # 기존 테스트 데이터 정리
    existing = db.query(User).filter(User.email == test_email).first()
    if existing:
        db.delete(existing)
        db.commit()
        print("  (이전 테스트 데이터 정리됨)")

    # User 생성 — google_sub 을 채우니 auth_provider='google' 로 설정 (CHECK 제약 만족)
    u = User(
        id=gen_uuid(),
        email=test_email,
        name="연결 테스트",
        google_sub=f"test_sub_{gen_uuid()[:8]}",
        auth_provider="google",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    print(f"  User 생성   : id={u.id[:8]}..., email={u.email}, "
          f"role={u.role}, provider={u.auth_provider}")

    # Session 생성
    s = InterviewSession(id=gen_uuid(), user_id=u.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    print(f"  Session 생성: id={s.id[:8]}..., public_code={s.public_code}, "
          f"is_shared={s.is_shared}")

    # 관계 조회
    db.refresh(u)
    print(f"  관계 조회   : User 의 sessions = {len(u.sessions)}개")

    # 로컬 가입 사용자도 검증 (username + password_hash 필수)
    from auth.password import hash_password, verify_password
    local_email = "_connection_test_local@local.dev"
    existing_local = db.query(User).filter(User.email == local_email).first()
    if existing_local:
        db.delete(existing_local)
        db.commit()

    local_user = User(
        id=gen_uuid(),
        email=local_email,
        username="conn_test_user",
        password_hash=hash_password("CapstonePassword123!"),
        name="로컬 테스트",
        auth_provider="local",
        role="user",
    )
    db.add(local_user)
    db.commit()
    db.refresh(local_user)
    print(f"  Local User  : username={local_user.username}, "
          f"role={local_user.role}, provider={local_user.auth_provider}")
    print(f"  비밀번호 검증(정답): {verify_password('CapstonePassword123!', local_user.password_hash)}")
    print(f"  비밀번호 검증(오답): {verify_password('wrong', local_user.password_hash)}")

    # 정리
    db.delete(u)         # google 사용자 + 자식 session cascade 삭제
    db.delete(local_user)
    db.commit()
    print("  (테스트 데이터 정리 완료)")
finally:
    db.close()

print()
print("=" * 50)
print("✓ Supabase 연결 + ORM 동작 정상")
print("=" * 50)
print()
print("다음 단계: main.py 의 SESSIONS dict 를 DB persist 로 전환")
