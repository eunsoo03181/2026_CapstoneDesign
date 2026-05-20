-- ============================================================
-- Migration 003 — questions: 사용자 만족도 평점
--
-- 1~5 점 (별점). null = 미평가.
-- 향후 "어떤 질문이 사용자에게 도움이 됐는지" 분석에 활용.
--
-- 실행: Supabase Dashboard → SQL Editor → 새 쿼리 → 붙여넣기 → Run
-- ============================================================

ALTER TABLE public.questions
  ADD COLUMN IF NOT EXISTS user_satisfaction smallint;

-- 값 범위 체크 (1~5 또는 null)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE constraint_name = 'questions_user_satisfaction_check'
  ) THEN
    ALTER TABLE public.questions
      ADD CONSTRAINT questions_user_satisfaction_check
      CHECK (user_satisfaction IS NULL OR (user_satisfaction BETWEEN 1 AND 5));
  END IF;
END $$;

-- 확인
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema='public' AND table_name='questions' AND column_name='user_satisfaction';
