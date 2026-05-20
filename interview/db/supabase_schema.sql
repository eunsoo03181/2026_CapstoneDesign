-- ============================================================
-- MockTalk Supabase 스키마 (v2)
-- 실행: Supabase Dashboard → SQL Editor 에서 한 번 실행
-- ============================================================

-- 1. users  (Supabase Auth 의 auth.users 와 연동되는 프로필 테이블)
CREATE TABLE IF NOT EXISTS public.users (
  id           uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  google_sub   text UNIQUE,
  email        text UNIQUE NOT NULL,
  name         text NOT NULL DEFAULT '',
  picture      text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- 2. resumes
CREATE TABLE IF NOT EXISTS public.resumes (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  filename      text NOT NULL,
  format        text NOT NULL,
  content_text  text NOT NULL,
  storage_path  text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_resumes_user ON public.resumes(user_id);

-- 3. interview_sessions
CREATE TABLE IF NOT EXISTS public.interview_sessions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  public_code         text UNIQUE NOT NULL,
  user_id             uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  resume_id           uuid REFERENCES public.resumes(id) ON DELETE SET NULL,
  status              text NOT NULL DEFAULT 'in_progress',
  is_shared           boolean NOT NULL DEFAULT false,
  share_token         text UNIQUE,
  final_score_100     numeric,
  content_score_80    numeric,
  nonverbal_score_20  numeric,
  started_at          timestamptz NOT NULL DEFAULT now(),
  completed_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON public.interview_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_public_code ON public.interview_sessions(public_code);

-- 4. questions
CREATE TABLE IF NOT EXISTS public.questions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id         uuid NOT NULL REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
  order_no           int  NOT NULL,
  question_id_str    text NOT NULL,
  text               text NOT NULL,
  intent             text NOT NULL DEFAULT '',
  evaluation_points  jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_questions_session ON public.questions(session_id);

-- 5. answers
CREATE TABLE IF NOT EXISTS public.answers (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id   uuid UNIQUE NOT NULL REFERENCES public.questions(id) ON DELETE CASCADE,
  audio_path    text,
  duration_sec  numeric,
  transcript    text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- 6. evaluations
CREATE TABLE IF NOT EXISTS public.evaluations (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id          uuid UNIQUE NOT NULL REFERENCES public.answers(id) ON DELETE CASCADE,
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
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  answer_id     uuid UNIQUE REFERENCES public.answers(id) ON DELETE CASCADE,
  session_id    uuid REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
  video_path    text NOT NULL,
  duration_sec  numeric,
  mime_type     text NOT NULL DEFAULT 'video/webm',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_video_session ON public.video_clips(session_id);

-- 8. nonverbal_metrics
CREATE TABLE IF NOT EXISTS public.nonverbal_metrics (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id          uuid UNIQUE NOT NULL REFERENCES public.interview_sessions(id) ON DELETE CASCADE,
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


-- ============================================================
-- Row Level Security (RLS)
-- ============================================================
ALTER TABLE public.users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resumes             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.interview_sessions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.questions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.answers             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evaluations         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.video_clips         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nonverbal_metrics   ENABLE ROW LEVEL SECURITY;

-- 본인 프로필 조회/수정
CREATE POLICY "users self"
  ON public.users FOR ALL
  USING (auth.uid() = id);

-- 이력서 — 본인 것만
CREATE POLICY "resumes owner"
  ON public.resumes FOR ALL
  USING (auth.uid() = user_id);

-- 세션 — 본인 것만 + 공유된 것은 누구나 SELECT
CREATE POLICY "sessions owner"
  ON public.interview_sessions FOR ALL
  USING (auth.uid() = user_id);

CREATE POLICY "sessions shared read"
  ON public.interview_sessions FOR SELECT
  USING (is_shared = true);

-- 자식 테이블 — 세션 소유자 또는 세션이 공유된 경우 SELECT
CREATE POLICY "questions via session"
  ON public.questions FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.interview_sessions s
      WHERE s.id = questions.session_id
        AND (s.user_id = auth.uid() OR s.is_shared = true)
    )
  );

CREATE POLICY "answers via session"
  ON public.answers FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.questions q
      JOIN public.interview_sessions s ON s.id = q.session_id
      WHERE q.id = answers.question_id
        AND (s.user_id = auth.uid() OR s.is_shared = true)
    )
  );

CREATE POLICY "evaluations via answer"
  ON public.evaluations FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.answers a
      JOIN public.questions q ON q.id = a.question_id
      JOIN public.interview_sessions s ON s.id = q.session_id
      WHERE a.id = evaluations.answer_id
        AND (s.user_id = auth.uid() OR s.is_shared = true)
    )
  );

CREATE POLICY "video_clips via session"
  ON public.video_clips FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.interview_sessions s
      WHERE s.id = video_clips.session_id
        AND (s.user_id = auth.uid() OR s.is_shared = true)
    )
  );

CREATE POLICY "nonverbal via session"
  ON public.nonverbal_metrics FOR ALL
  USING (
    EXISTS (
      SELECT 1 FROM public.interview_sessions s
      WHERE s.id = nonverbal_metrics.session_id
        AND (s.user_id = auth.uid() OR s.is_shared = true)
    )
  );


-- ============================================================
-- 신규 가입 시 public.users 자동 생성 트리거
-- (Supabase Auth 의 auth.users 에 row 추가될 때 동기화)
-- ============================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger AS $$
BEGIN
  INSERT INTO public.users (id, email, name, picture, google_sub)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
    NEW.raw_user_meta_data->>'avatar_url',
    NEW.raw_user_meta_data->>'sub'
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
