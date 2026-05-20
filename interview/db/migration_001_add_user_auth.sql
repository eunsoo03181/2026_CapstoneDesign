-- ============================================================
-- Migration 001 — users 테이블 확장: 로컬 로그인 + 권한 등급
--
-- 실행: Supabase Dashboard → SQL Editor → 새 쿼리 → 붙여넣기 → Run
-- 안전: ADD COLUMN IF NOT EXISTS / IDEMPOTENT — 여러 번 실행해도 OK
-- ============================================================

-- 1) users 테이블에 컬럼 추가
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS username       varchar(50)  UNIQUE,
  ADD COLUMN IF NOT EXISTS password_hash  text,
  ADD COLUMN IF NOT EXISTS phone          varchar(20),
  ADD COLUMN IF NOT EXISTS auth_provider  varchar(20)  NOT NULL DEFAULT 'local',
  ADD COLUMN IF NOT EXISTS role           varchar(20)  NOT NULL DEFAULT 'user',
  ADD COLUMN IF NOT EXISTS is_active      boolean      NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS last_login_at  timestamptz;

-- 2) 인덱스 추가
CREATE INDEX IF NOT EXISTS idx_users_username      ON public.users(username);
CREATE INDEX IF NOT EXISTS idx_users_role          ON public.users(role);
CREATE INDEX IF NOT EXISTS idx_users_auth_provider ON public.users(auth_provider);

-- 3) 데이터 무결성 체크 (CHECK 제약)
DO $$
BEGIN
  -- 이미 있으면 무시
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'users_role_check'
  ) THEN
    ALTER TABLE public.users
      ADD CONSTRAINT users_role_check
      CHECK (role IN ('user', 'moderator', 'admin'));
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'users_auth_provider_check'
  ) THEN
    ALTER TABLE public.users
      ADD CONSTRAINT users_auth_provider_check
      CHECK (auth_provider IN ('local', 'google', 'kakao'));
  END IF;

  -- 로컬 사용자는 username + password_hash 필수
  -- 외부 OAuth 사용자는 둘 다 NULL 허용
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'users_local_credentials_check'
  ) THEN
    ALTER TABLE public.users
      ADD CONSTRAINT users_local_credentials_check
      CHECK (
        auth_provider <> 'local'
        OR (username IS NOT NULL AND password_hash IS NOT NULL)
      );
  END IF;
END $$;

-- 4) 확인용 출력
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'users'
ORDER BY ordinal_position;
