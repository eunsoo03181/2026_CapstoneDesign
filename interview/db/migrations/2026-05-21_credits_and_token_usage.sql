-- =============================================================================
-- Migration: credits + token_usage + (잔존) suspended_until
-- 대상: Postgres / Supabase (SQLite 는 init_db() 가 자동 처리)
--
-- 실행 방법
--   Supabase 대시보드 → SQL Editor → New query → 아래 전체 붙여넣기 → RUN
--   (또는 psql:  psql $DATABASE_URL -f db/migrations/2026-05-21_credits_and_token_usage.sql)
--
-- 안전성
--   모든 DDL 에 IF NOT EXISTS / IF NOT EXISTS COLUMN — 여러 번 실행해도 멱등.
--   기존 데이터는 영향 없음.
-- =============================================================================


-- ─── 1) users.credits + suspended_until 컬럼 ──────────────────────────────────
-- 면접 1회 생성에 1 credit 소비. admin/moderator 는 차감 없음.
-- 신규 가입자는 0 — 관리자가 수동 부여해야 함.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS credits INTEGER NOT NULL DEFAULT 0;

-- suspended_until — 임시 정지 만료 시각 (null = 영구 정지)
-- 이미 있으면 IF NOT EXISTS 가 무시함.
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS suspended_until TIMESTAMP NULL;


-- ─── 2) credit_transactions — 입출금 audit log ────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_transactions (
    id                    VARCHAR(32)  PRIMARY KEY,
    user_id               VARCHAR(32)  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta                 INTEGER      NOT NULL,
    balance_after         INTEGER      NOT NULL,
    reason                VARCHAR(120) NOT NULL DEFAULT '',
    related_session_code  VARCHAR(32)  NULL,
    created_by_user_id    VARCHAR(32)  NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at            TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_tx_user
    ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_session
    ON credit_transactions(related_session_code);
CREATE INDEX IF NOT EXISTS idx_credit_tx_created
    ON credit_transactions(created_at);


-- ─── 3) token_usage — OpenAI / Whisper 호출별 토큰·비용 ──────────────────────
CREATE TABLE IF NOT EXISTS token_usage (
    id                 VARCHAR(32) PRIMARY KEY,
    user_id            VARCHAR(32) NULL REFERENCES users(id) ON DELETE SET NULL,
    session_id         VARCHAR(32) NULL,                   -- public_code (FK 아님, NULL 허용)
    endpoint           VARCHAR(64) NOT NULL,               -- 'answer_evaluator', 'question_generator', ...
    model              VARCHAR(64) NOT NULL,               -- 'gpt-4o-mini', 'whisper-1', ...
    prompt_tokens      INTEGER     NOT NULL DEFAULT 0,
    completion_tokens  INTEGER     NOT NULL DEFAULT 0,
    total_tokens       INTEGER     NOT NULL DEFAULT 0,
    audio_seconds      DOUBLE PRECISION NULL,              -- Whisper 만
    cost_usd           DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at         TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_token_usage_user
    ON token_usage(user_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_session
    ON token_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_token_usage_endpoint
    ON token_usage(endpoint);
CREATE INDEX IF NOT EXISTS idx_token_usage_model
    ON token_usage(model);
CREATE INDEX IF NOT EXISTS idx_token_usage_created
    ON token_usage(created_at);


-- =============================================================================
-- 확인 쿼리 (선택)
--   SELECT column_name, data_type FROM information_schema.columns
--     WHERE table_name = 'users' AND column_name IN ('credits','suspended_until');
--   SELECT to_regclass('public.credit_transactions');
--   SELECT to_regclass('public.token_usage');
-- =============================================================================
