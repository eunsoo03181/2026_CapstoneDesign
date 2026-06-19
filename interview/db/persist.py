"""
면접 세션을 메모리 dict → DB 로 영속화.

main.py 의 SESSIONS dict 에 담긴 인터뷰 진행 결과를
interview_sessions / resumes / questions / answers / evaluations
테이블로 저장한다.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from sqlalchemy.orm import Session

from .models import (
    User, Resume, InterviewSession, Question, Answer, Evaluation,
    NonverbalMetrics, VideoClip,
)
from .utils import gen_uuid


def persist_finalized_session(
    db: Session,
    *,
    user: User,
    public_code: str,
    resume_text: str,
    resume_filename: Optional[str],
    resume_format: Optional[str],
    audio_dir: Optional[str],
    questions_data: list,       # [{question_id, question, intent, evaluation_points}, ...]
    answers_data: list,         # [(question_dict, transcript_str), ...]
    evaluation: dict,           # answer_evaluator 의 evaluate_session 결과
    final: dict,                # final_score 의 compute_final_score 결과
    nonverbal_metrics: Optional[dict] = None,   # face_analyzer 결과 (옵션)
    nonverbal_feedback: Optional[dict] = None,  # OpenAI 4o-mini 피드백 (옵션)
    voice_eval: Optional[dict] = None,                 # 음성 비언어 세션 집계 (옵션)
    voice_per_question: Optional[list] = None,         # 답변별 음성 비언어 평가 (옵션)
    consistency_checks: Optional[list] = None,         # 답변별 일관성 검증 결과 (옵션)
    company_research: Optional[dict] = None,           # 회사·직무 리서치 결과 (옵션)
    video_paths: Optional[list] = None,   # [None | "supabase:..."] 질문 인덱스별
    model_used: Optional[str] = None,     # 답변 평가에 사용한 OpenAI 모델 이름
) -> InterviewSession:
    """
    finalize 시점에 모든 데이터를 한 트랜잭션으로 저장.

    반환: 생성된 InterviewSession (DB persisted).
    """
    now = datetime.utcnow()

    # 1) Resume row
    resume_row = Resume(
        id=gen_uuid(),
        user_id=user.id,
        filename=resume_filename or "resume.txt",
        format=resume_format or ".txt",
        content_text=resume_text,
        storage_path=None,
    )
    db.add(resume_row)
    db.flush()

    # 2) InterviewSession row (public_code 는 미리 정한 것 사용)
    session_row = InterviewSession(
        id=gen_uuid(),
        public_code=public_code,
        user_id=user.id,
        resume_id=resume_row.id,
        status="completed",
        is_shared=False,
        share_includes_audio=False,
        share_includes_video=False,
        share_includes_resume=False,
        is_deleted=False,
        audio_dir=audio_dir,
        model_used=model_used,
        final_score_100=final.get("final_score_100"),
        content_score_80=final.get("content_score_80"),
        nonverbal_score_20=final.get("nonverbal_score_20"),
        started_at=now,
        completed_at=now,
    )
    db.add(session_row)
    db.flush()

    # 3) Questions + Answers + Evaluations
    eval_items = (evaluation or {}).get("items", [])
    for i, ((q_obj, transcript), eval_item) in enumerate(
        zip(answers_data, eval_items + [None] * max(0, len(answers_data) - len(eval_items))),
        start=1,
    ):
        # 질문 — dict 또는 str 호환
        if isinstance(q_obj, dict):
            q_text   = q_obj.get("question", "")
            q_intent = q_obj.get("intent", "")
            q_eps    = q_obj.get("evaluation_points") or []
            q_idstr  = q_obj.get("question_id", f"Q{i}")
        else:
            q_text, q_intent, q_eps, q_idstr = str(q_obj), "", [], f"Q{i}"

        question_row = Question(
            id=gen_uuid(),
            session_id=session_row.id,
            order_no=i,
            question_id_str=q_idstr,
            text=q_text,
            intent=q_intent,
            evaluation_points=q_eps,
        )
        db.add(question_row)
        db.flush()

        # Answer
        audio_path = None
        if audio_dir:
            wav_candidate = Path(audio_dir) / f"answer_{i}.wav"
            webm_candidate = Path(audio_dir) / f"answer_{i}.webm"
            if wav_candidate.exists():
                audio_path = str(wav_candidate)
            elif webm_candidate.exists():
                audio_path = str(webm_candidate)

        answer_row = Answer(
            id=gen_uuid(),
            question_id=question_row.id,
            audio_path=audio_path,
            duration_sec=None,
            transcript=transcript or "",
        )
        db.add(answer_row)
        db.flush()

        # Evaluation
        if eval_item:
            eval_row = Evaluation(
                id=gen_uuid(),
                answer_id=answer_row.id,
                content_score=float(eval_item.get("content_score", 0)),
                common_subtotal=float(eval_item.get("common_subtotal", 0)),
                custom_subtotal=float(eval_item.get("custom_subtotal", 0)),
                common_scores=eval_item.get("common_scores") or {},
                custom_scores=eval_item.get("custom_scores") or [],
                strengths=eval_item.get("strengths") or [],
                improvements=eval_item.get("improvements") or [],
                content_feedback=eval_item.get("content_feedback") or "",
                sample_answer=eval_item.get("sample_answer") or "",
            )
            db.add(eval_row)

        # VideoClip (Supabase URI)
        if video_paths and i - 1 < len(video_paths):
            v_uri = video_paths[i - 1]
            if v_uri:
                video_row = VideoClip(
                    id=gen_uuid(),
                    answer_id=answer_row.id,
                    session_id=session_row.id,
                    video_path=v_uri,
                    duration_sec=None,
                    mime_type="video/webm",
                )
                db.add(video_row)

    # NonverbalMetrics — 시각/음성/일관성 중 하나라도 있으면 저장.
    # (옛 흐름은 카메라 ON 만 저장했으나, 음성·일관성 데이터도 같은 row 의
    #  raw_metrics_json 에 묶어 저장해 결과 화면에서 한 번에 꺼냄.)
    has_visual = bool(nonverbal_metrics and nonverbal_metrics.get("ok"))
    has_voice = bool(voice_eval and voice_eval.get("ok"))
    has_consistency = bool(consistency_checks)
    # 회사 입력만 있어도 row 생성 (사용자가 회사 검색 결과를 결과 페이지에서 확인 가능하도록)
    has_company = bool(
        company_research
        and (company_research.get("company_name")
             or company_research.get("job_title")
             or company_research.get("company_job_summary"))
    )

    if has_visual or has_voice or has_consistency or has_company:
        nm_dict = nonverbal_metrics if has_visual else {}
        m = (nm_dict.get("metrics") or {}) if has_visual else {}
        s = (nm_dict.get("scores_20") or {}) if has_visual else {}

        bundle: dict = {**(nm_dict or {})}
        if nonverbal_feedback:
            bundle["feedback"] = nonverbal_feedback
        if voice_eval:
            bundle["voice_eval"] = voice_eval
        if voice_per_question:
            bundle["voice_per_question"] = voice_per_question
        if consistency_checks:
            bundle["consistency_checks"] = consistency_checks
        if company_research:
            bundle["company_research"] = company_research

        # 옛 4축 호환: focus_score/blink_score 컬럼은 분해 컴포넌트가 있으면 거기서,
        # 없으면 통합 gaze 에서 절반씩 추정 (대시보드 합산만 정확하면 됨)
        focus_score = float((s.get("_focus_component") or s.get("focus") or {}).get("score", 0))
        blink_score = float((s.get("_blink_component") or s.get("blink") or {}).get("score", 0))
        if not focus_score and not blink_score and s.get("gaze"):
            gz = float((s.get("gaze") or {}).get("score", 0))
            focus_score = gz * 0.6
            blink_score = gz * 0.4

        nm = NonverbalMetrics(
            id=gen_uuid(),
            session_id=session_row.id,
            score_20=float(nm_dict.get("score_20", 0)) if has_visual else 0.0,
            smile_score=float((s.get("smile") or {}).get("score", 0)),
            focus_score=focus_score,
            blink_score=blink_score,
            posture_score=float((s.get("posture") or {}).get("score", 0)),
            smile_ratio=float(m.get("smile_ratio", 0)),
            focus_ratio=float(m.get("focus_ratio", 0)),
            blink_per_minute=float(m.get("blink_per_minute", 0)),
            avg_movement_px=float(m.get("avg_movement_px", 0)),
            duration_sec=float(nm_dict.get("duration_sec", 0)) if has_visual else 0.0,
            silent_sec=0.0,
            speak_sec=0.0,
            raw_metrics_json=bundle,
        )
        db.add(nm)

    db.commit()
    db.refresh(session_row)
    return session_row
