-- ============================================================
-- Migration 002 — interview_sessions: 공유 옵션 + 소프트 삭제
--
-- 실행: Supabase Dashboard → SQL Editor → 새 쿼리 → 붙여넣기 → Run
-- 안전: ADD COLUMN IF NOT EXISTS / IDEMPOTENT — 여러 번 실행해도 OK
-- ============================================================

ALTER TABLE public.interview_sessions
  -- 소프트 삭제: 데이터는 남기고 본인/공유 접근만 차단
  ADD COLUMN IF NOT EXISTS is_deleted    boolean      NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS deleted_at    timestamptz,

  -- 공유 시 무엇까지 노출할지 선택 (is_shared=true 일 때만 의미)
  --   기본: Q&A 텍스트만 공개 (셋 모두 false)
  ADD COLUMN IF NOT EXISTS share_includes_audio  boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS share_includes_video  boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS share_includes_resume boolean NOT NULL DEFAULT false,

  -- 면접 진행 중 임시 데이터 (메모리 대신 DB 에 보관할 때 사용)
  ADD COLUMN IF NOT EXISTS audio_dir     varchar(500);    -- 음성 wav 가 저장된 폴더 경로

CREATE INDEX IF NOT EXISTS idx_sessions_is_deleted ON public.interview_sessions(is_deleted);
CREATE INDEX IF NOT EXISTS idx_sessions_user_active
  ON public.interview_sessions(user_id, is_deleted, created_at DESC);

-- 확인용
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema='public' AND table_name='interview_sessions'
  AND column_name IN (
    'is_deleted','deleted_at',
    'share_includes_audio','share_includes_video','share_includes_resume',
    'audio_dir'
  )
ORDER BY column_name;
