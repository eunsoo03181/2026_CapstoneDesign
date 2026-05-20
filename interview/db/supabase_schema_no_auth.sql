-- ============================================================
-- MockTalk Supabase 스키마 (v2-pre, 인증 미포함)
--
-- 이 버전은 Supabase Auth 연동 전에 우선 테이블만 만드는 용도.
-- 인증 붙일 때 supabase_schema.sql 의 RLS / trigger 부분만 추가 실행하면 됨.
--
-- 실행: Supabase Dashboard → SQL Editor → New query → 붙여넣기 → Run
-- ============================================================

-- 1. users  (Supabase Auth 의 auth.users 참조 없이 독립)
CREATE TABLE IF NOT EXISTS public.users (
  id           varchar(32) PRIMARY KEY,
  google_sub   varchar(255) UNIQUE,
  email        varchar(255) UNIQUE NOT NULL,
  name         varchar(100) NOT NULL DEFAULT '',
  picture      varchar(500),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 2. resumes
CREATE TABLE IF NOT EXISTS public.resumes (
  id            varchar(32) PRIMARY KEY,
  user_id       varchar(32) NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  filename      varchar(255) NOT NULL,
  format        varchar(10)  NOT NULL,
  content_text  text NOT NULL,
  storage_path  varchar(500),
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON public.resumes(user_id);

-- 3. interview_sessions
CREATE TABLE IF NOT EXISTS public.interview_sessions (
  id                  varchar(32) PRIMARY KEY,
  public_code         varchar(12) UNIQUE NOT NULL,
  user_id             varchar(32) NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  resume_id           varchar(32) REFERENCES public.resumes(id) ON DELETE SET NULL,
  status              varchar(20) NOT NULL DEFAULT 'in_progress',
  is_shared           boolean NOT NULL DEFAULT false,
  share_token         varchar(32) UNIQUE,
  final_score_100     numeric,
  content_score_80    numeric,
  nonverbal_score_20  numeric,
  started_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user        ON public.interview_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_public_code ON public.interview_sessions(public_code);

-- 4. questions
CREATE TABLE IF NOT EXISTS public.questions (
  id                 varchar(32) PRIMARY KEY,
  session_id         varchar(32) NOT NULL REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
  order_no           int  NOT NULL,
  question_id_str    varchar(20) NOT NULL,
  text               text NOT NULL,
  intent             text NOT NULL DEFAULT '',
  evaluation_points  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_questions_session ON public.questions(session_id);

-- 5. answers
CREATE TABLE IF NOT EXISTS public.answers (
  id            varchar(32) PRIMARY KEY,
  question_id   varchar(32) UNIQUE NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  audio_path    varchar(500),
  duration_sec  numeric,
  transcript    text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 6. evaluations
CREATE TABLE IF NOT EXISTS public.evaluations (
  id                 varchar(32) PRIMARY KEY,
  answer_id          varchar(32) UNIQUE NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
  content_score      numeric NOT NULL,
  common_subtotal    numeric NOT NULL,
  custom_subtotal    numeric NOT NULL,
  common_scores      jsonb   NOT NULL DEFAULT '{}'::jsonb,
  custom_scores      jsonb   NOT NULL DEFAULT '[]'::jsonb,
  strengths          jsonb   NOT NULL DEFAULT '[]'::jsonb,
  improvements       jsonb   NOT NULL DEFAULT '[]'::jsonb,
  content_feedback   text    NOT NULL DEFAULT '',
  sample_answer      text    NOT NULL DEFAULT '',
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- 7. video_clips
CREATE TABLE IF NOT EXISTS public.video_clips (
  id            varchar(32) PRIMARY KEY,
  answer_id     varchar(32) UNIQUE REFERENCES public.answers(id) ON DELETE CASCADE,
  session_id    varchar(32) REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
  video_path    varchar(500) NOT NULL,
  duration_sec  numeric,
  mime_type     varchar(50) NOT NULL DEFAULT 'video/webm',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_video_session ON public.video_clips(session_id);

-- 8. nonverbal_metrics
CREATE TABLE IF NOT EXISTS public.nonverbal_metrics (
  id                  varchar(32) PRIMARY KEY,
  session_id          varchar(32) UNIQUE NOT NULL REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
  score_20            numeric NOT NULL,
  smile_score         numeric NOT NULL,
  focus_score         numeric NOT NULL,
  blink_score         numeric NOT NULL,
  posture_score       numeric NOT NULL,
  smile_ratio         numeric NOT NULL,
  focus_ratio         numeric NOT NULL,
  blink_per_minute    numeric NOT NULL,
  avg_movement_px     numeric NOT NULL,
  duration_sec        numeric NOT NULL,
  silent_sec          numeric NOT NULL,
  speak_sec           numeric NOT NULL,
  raw_metrics_json    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- 확인용: 8개 테이블 생성 결과 출력
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'users', 'resumes', 'interview_sessions', 'questions',
    'answers', 'evaluations', 'video_clips', 'nonverbal_metrics'
  )
ORDER BY table_name;
