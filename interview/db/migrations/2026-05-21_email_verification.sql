-- =============================================================================
-- Migration: 이메일 인증
-- 대상: Postgres / Supabase (SQLite 는 init_db() 자동 처리)
--
-- 실행: Supabase SQL Editor → 붙여넣기 → RUN
-- 멱등 (IF NOT EXISTS) — 여러 번 실행 안전.
-- =============================================================================


-- ─── 1) users.email_verified 컬럼 ─────────────────────────────────────────────
-- 로컬 회원가입: 기본 FALSE, 인증 메일 링크 클릭 시 TRUE
-- Google OAuth 가입: 가입/로그인 시 자동 TRUE (Google 이 검증)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- 기존 가입자는 grandfather — 인증된 것으로 간주 (이미 정상 사용 중인 계정)
-- 새로 마이그레이션 직후 1회만 의미 있음. 이후 신규 가입자는 default FALSE.
UPDATE users SET email_verified = TRUE
  WHERE email_verified = FALSE;


-- ─── 2) email_verification_tokens 테이블 ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id           VARCHAR(32) PRIMARY KEY,
    user_id      VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token        VARCHAR(64) NOT NULL UNIQUE,
    expires_at   TIMESTAMP   NOT NULL,
    used_at      TIMESTAMP   NULL,
    created_at   TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_verify_user    ON email_verification_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_email_verify_token   ON email_verification_tokens(token);
CREATE INDEX IF NOT EXISTS idx_email_verify_expires ON email_verification_tokens(expires_at);
