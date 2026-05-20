# Signal Catch 버전 노트

## v1 — Local SQLite (임시본, 2026-05)

현재 상태:
- **DB**: 로컬 SQLite (`mocktalk.db`)
- **인증**: Google OAuth 직접 구현 (authlib)
- **파일 저장**: 로컬 `sessions/` 폴더
- **세션 저장**: 메모리 dict (`SESSIONS`) — DB 통합 전

동작 환경변수:
```
OPENAI_API_KEY=sk-proj-...
DATABASE_URL=sqlite:///mocktalk.db   # 또는 미지정 시 기본값
GOOGLE_CLIENT_ID=...   # OAuth 사용 시
GOOGLE_CLIENT_SECRET=...
SESSION_SECRET_KEY=...
```

실행: `uvicorn main:app --reload`

---

## v2 — Supabase (개발 중)

전환 내용:
- **DB**: SQLite → Supabase PostgreSQL (DATABASE_URL 만 교체)
- **인증**: 자체 OAuth → Supabase Auth (Google 토글 ON)
- **파일 저장**: 로컬 → Supabase Storage (서명 URL)
- **권한**: 코드 체크 → Row Level Security (RLS) DB 강제
- **세션**: 메모리 dict → DB persist (`interview_sessions` 테이블)

전환 시 환경변수:
```
OPENAI_API_KEY=sk-proj-...
DATABASE_URL=postgresql://...supabase.com:6543/postgres
SUPABASE_URL=https://[ref].supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_KEY=eyJ...   # 서버 전용, 클라이언트 노출 X
```

코드 변경 범위: `db/`, `auth/`, `main.py` 일부 + 신규 `storage.py`.
SQLAlchemy 모델은 그대로 — Supabase 가 PostgreSQL 이라 ORM 코드 무변경.
