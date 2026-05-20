-- Migration 006: 정지 기간 + 면접 모델 추적
--   - users.suspended_until : 일정 기간 정지용 (null 이면 영구)
--   - interview_sessions.model_used : 답변 평가에 사용한 OpenAI 모델 이름
-- Supabase SQL Editor 에서 1회 실행.

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS suspended_until TIMESTAMP;

ALTER TABLE interview_sessions
  ADD COLUMN IF NOT EXISTS model_used VARCHAR(40);
