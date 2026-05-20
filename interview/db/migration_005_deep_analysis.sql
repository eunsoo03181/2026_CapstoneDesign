-- Migration 005: '심층 분석' 결과 저장용 컬럼 추가
-- gpt-5.5 가 생성한 markdown 텍스트와, 사용된 모델/시각 메타.
-- Supabase SQL Editor 에서 1회 실행.

-- 005 + 006 합본 (멱등 — 이미 있어도 안전)
ALTER TABLE interview_sessions
  ADD COLUMN IF NOT EXISTS deep_analysis_md    TEXT,
  ADD COLUMN IF NOT EXISTS deep_analysis_model VARCHAR(40),
  ADD COLUMN IF NOT EXISTS deep_analysis_at    TIMESTAMP,
  ADD COLUMN IF NOT EXISTS model_used          VARCHAR(40);

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS suspended_until TIMESTAMP;