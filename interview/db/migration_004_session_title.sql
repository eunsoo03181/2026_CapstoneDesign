-- ============================================================
-- Migration 004 — interview_sessions: 사용자 지정 제목
--
-- 기본값 null. 사용자가 면접 기록에 자유 제목을 붙일 수 있도록.
-- 비어있으면 UI 가 자동으로 "면접 #공개코드" 형태로 표시.
-- ============================================================

ALTER TABLE public.interview_sessions
  ADD COLUMN IF NOT EXISTS title varchar(200);

-- 확인
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema='public' AND table_name='interview_sessions' AND column_name='title';
