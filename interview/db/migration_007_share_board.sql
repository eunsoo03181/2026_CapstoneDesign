-- Migration 007: 공유 게시판 등록 토글
--   - interview_sessions.list_on_board : true 이면 /board 페이지의 목록에 노출.
--                                         is_shared 가 false 면 의미 없음.
-- Supabase SQL Editor 에서 1회 실행.

ALTER TABLE interview_sessions
  ADD COLUMN IF NOT EXISTS list_on_board BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_interview_sessions_board
  ON interview_sessions (list_on_board, is_deleted, completed_at DESC)
  WHERE list_on_board = TRUE AND is_deleted = FALSE;
