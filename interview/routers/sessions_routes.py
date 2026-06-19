"""
면접 세션 라우터 — 목록 / 상세 / 공유 / 삭제 / 파일 다운로드.

권한 정책:
  - 목록 (/api/sessions)              : 본인 (admin 은 ?all=1 로 전체)
  - 상세 (/api/sessions/{code})       : 본인 또는 admin (소프트 삭제된 것은 본인도 X, admin 만)
  - 공유 보기 (/api/sessions/{code}/public)  : is_shared=True 이고 not deleted 인 경우 누구나
  - 공유 토글/옵션 변경                : 본인만
  - 소프트 삭제                       : 본인 또는 admin
  - 음성/이력서/리포트 파일 다운로드   : 본인/admin / 공유 시 옵션에 따라
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db, User, InterviewSession, Question, Answer, Evaluation
from auth.deps import get_current_user, get_current_user_optional


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ---------- 응답 스키마 ----------

class SessionSummary(BaseModel):
    public_code: str
    title: Optional[str] = None
    status: str
    final_score_100: Optional[float]
    content_score_80: Optional[float]
    nonverbal_score_20: Optional[float]
    has_nonverbal: bool = False     # 카메라 사용 면접만 True — UI 가 /100 vs /80 분기에 사용
    n_questions: int
    is_shared: bool
    is_deleted: bool
    model_used: Optional[str] = None
    completed_at: Optional[datetime]
    created_at: datetime


class SessionTitleBody(BaseModel):
    title: Optional[str] = None


class ShareOptions(BaseModel):
    is_shared: bool
    share_includes_audio: bool
    share_includes_video: bool
    share_includes_resume: bool
    list_on_board: bool = False


class ShareOptionsPatch(BaseModel):
    """부분 업데이트용 — 모든 필드 선택. 누락된 필드는 기존 값 유지."""
    is_shared: Optional[bool] = None
    share_includes_audio: Optional[bool] = None
    share_includes_video: Optional[bool] = None
    share_includes_resume: Optional[bool] = None
    list_on_board: Optional[bool] = None


# ---------- 헬퍼 ----------

def _get_session_or_404(db: Session, public_code: str) -> InterviewSession:
    s = (
        db.query(InterviewSession)
        .filter(InterviewSession.public_code == public_code)
        .first()
    )
    if not s:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    return s


def _can_view_as_owner_or_admin(s: InterviewSession, user: Optional[User]) -> bool:
    if not user:
        return False
    if user.role == "admin":
        return True
    return s.user_id == user.id


def _ensure_owner_or_admin(s: InterviewSession, user: User):
    if not _can_view_as_owner_or_admin(s, user):
        raise HTTPException(403, "접근 권한이 없습니다.")


def _build_full_detail(s: InterviewSession) -> dict:
    """소유자/admin 용 — 모든 필드 노출."""
    questions = []
    for q in s.questions:
        a = q.answer
        e = a.evaluation if a else None
        questions.append({
            "order_no":           q.order_no,
            "question_id_str":    q.question_id_str,
            "text":               q.text,
            "intent":             q.intent,
            "evaluation_points":  q.evaluation_points,
            "user_satisfaction":  q.user_satisfaction,
            "transcript":         a.transcript if a else "",
            "has_audio":          bool(a and a.audio_path),
            "has_video":          bool(a and a.video_clip and a.video_clip.video_path),
            "evaluation":         {
                "content_score":     e.content_score,
                "common_subtotal":   e.common_subtotal,
                "custom_subtotal":   e.custom_subtotal,
                "common_scores":     e.common_scores,
                "custom_scores":     e.custom_scores,
                "strengths":         e.strengths,
                "improvements":      e.improvements,
                "content_feedback":  e.content_feedback,
                "sample_answer":     e.sample_answer,
            } if e else None,
        })

    # 비언어 metrics + 피드백 (저장된 경우)
    nv = s.nonverbal_metrics
    nonverbal_payload = None
    nonverbal_feedback = None
    voice_eval = None
    voice_per_question: list = []
    consistency_checks: list = []
    company_research = None
    if nv:
        raw = nv.raw_metrics_json or {}
        # 시각 비언어 — score_20 > 0 또는 metrics 가 있을 때만 유효 표시
        visual_ok = bool(
            (isinstance(raw, dict) and raw.get("ok"))
            or nv.score_20 > 0
        )
        if visual_ok:
            nonverbal_payload = {
                "ok": True,
                "duration_sec": nv.duration_sec,
                "score_20": nv.score_20,
                "scores_20": (raw.get("scores_20") if isinstance(raw, dict) else None) or {
                    "smile":   {"score": nv.smile_score,   "max": 6, "label": ""},
                    "focus":   {"score": nv.focus_score,   "max": 6, "label": ""},
                    "blink":   {"score": nv.blink_score,   "max": 4, "label": ""},
                    "posture": {"score": nv.posture_score, "max": 4, "label": ""},
                },
                "metrics": (raw.get("metrics") if isinstance(raw, dict) else None) or {
                    "smile_ratio": nv.smile_ratio,
                    "focus_ratio": nv.focus_ratio,
                    "blink_per_minute": nv.blink_per_minute,
                    "avg_movement_px": nv.avg_movement_px,
                },
                # 결과 화면 차트용 — N초 간격 누적 지표 (옛 세션엔 없음)
                "timeline": (raw.get("timeline") if isinstance(raw, dict) else None) or [],
            }
        if isinstance(raw, dict):
            if raw.get("feedback"):
                nonverbal_feedback = raw["feedback"]
            voice_eval = raw.get("voice_eval")
            voice_per_question = raw.get("voice_per_question") or []
            consistency_checks = raw.get("consistency_checks") or []
            company_research = raw.get("company_research")

    return {
        "public_code": s.public_code,
        "title": s.title,
        "status": s.status,
        "is_shared": s.is_shared,
        "is_deleted": s.is_deleted,
        "share_includes_audio": s.share_includes_audio,
        "share_includes_video": s.share_includes_video,
        "share_includes_resume": s.share_includes_resume,
        "final_score_100":     s.final_score_100,
        "content_score_80":    s.content_score_80,
        "nonverbal_score_20":  s.nonverbal_score_20,
        "model_used":   s.model_used,
        "started_at":   s.started_at,
        "completed_at": s.completed_at,
        "created_at":   s.created_at,
        "resume": {
            "filename":     s.resume.filename if s.resume else None,
            "format":       s.resume.format if s.resume else None,
            "content_text": s.resume.content_text if s.resume else None,
        } if s.resume else None,
        "questions": questions,
        "nonverbal_metrics": nonverbal_payload,
        "nonverbal_feedback": nonverbal_feedback,
        # 신규: 음성 비언어 + 답변 일관성 검증 + 회사 리서치
        "voice_eval": voice_eval,
        "voice_per_question": voice_per_question,
        "consistency_checks": consistency_checks,
        "company_research": company_research,
    }


def _build_public_view(s: InterviewSession) -> dict:
    """공유 뷰 — share_includes_* 플래그에 따라 정보 노출 조정."""
    full = _build_full_detail(s)

    # 이력서 — 공유 옵션 따라
    if not s.share_includes_resume and full.get("resume"):
        full["resume"] = {
            "filename":     full["resume"]["filename"],
            "format":       full["resume"]["format"],
            "content_text": None,   # 본문 가림
        }

    # 음성/영상 — 공유 옵션에 따라 has_audio / has_video 노출 결정
    for q in full["questions"]:
        if not s.share_includes_audio:
            q["has_audio"] = False
        if not s.share_includes_video:
            q["has_video"] = False

    return full


# ============================================================
# 목록 / 상세
# ============================================================

@router.get("", response_model=List[SessionSummary])
def list_sessions(
    all: bool = Query(False, description="admin 전용 — 전체 사용자 세션"),
    include_deleted: bool = Query(False, description="삭제된 세션도 포함 (admin 만)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 면접 세션 목록 (최신순). admin 은 all=1 로 전체 조회 가능."""
    q = db.query(InterviewSession)
    if all and current_user.role == "admin":
        pass
    else:
        q = q.filter(InterviewSession.user_id == current_user.id)

    if not (include_deleted and current_user.role == "admin"):
        q = q.filter(InterviewSession.is_deleted == False)  # noqa: E712

    rows = q.order_by(InterviewSession.created_at.desc()).all()
    out = []
    for s in rows:
        out.append(SessionSummary(
            public_code=s.public_code,
            title=s.title,
            status=s.status,
            final_score_100=s.final_score_100,
            content_score_80=s.content_score_80,
            nonverbal_score_20=s.nonverbal_score_20,
            has_nonverbal=(s.nonverbal_metrics is not None),
            n_questions=len(s.questions),
            is_shared=s.is_shared,
            is_deleted=s.is_deleted,
            model_used=s.model_used,
            completed_at=s.completed_at,
            created_at=s.created_at,
        ))
    return out


@router.get("/{public_code}")
def get_session_detail(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인/admin 전용 — 세션 전체 상세 (Q&A + 평가)."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    # 본인이라도 삭제된 건 안 보이게 (admin 은 보임)
    if s.is_deleted and current_user.role != "admin":
        raise HTTPException(404, "삭제된 세션입니다.")
    return _build_full_detail(s)


@router.get("/{public_code}/public")
def get_session_public_view(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """공유 뷰 — is_shared=True 이고 삭제되지 않은 경우 누구나 조회."""
    s = _get_session_or_404(db, public_code)
    if s.is_deleted:
        raise HTTPException(404, "삭제된 세션입니다.")
    if not s.is_shared:
        # 소유자/admin 은 공유 안 되어 있어도 본인용으로 접근 허용 (UX)
        if _can_view_as_owner_or_admin(s, current_user):
            return _build_full_detail(s)
        raise HTTPException(403, "비공개 세션입니다.")
    return _build_public_view(s)


# ============================================================
# 공유 옵션
# ============================================================

@router.get("/{public_code}/share", response_model=ShareOptions)
def get_share_options(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 — 현재 공유 옵션 조회."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    return ShareOptions(
        is_shared=s.is_shared,
        share_includes_audio=s.share_includes_audio,
        share_includes_video=s.share_includes_video,
        share_includes_resume=s.share_includes_resume,
        list_on_board=s.list_on_board,
    )


@router.put("/{public_code}/share", response_model=ShareOptions)
def update_share_options(
    public_code: str,
    opts: ShareOptionsPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인/admin — 공유 옵션 수정 (부분 업데이트).

    - 누락된 필드는 기존 값 유지.
    - is_shared=False 로 끄면 includes_* + list_on_board 도 자동 false 로 정리.
    """
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if s.is_deleted:
        raise HTTPException(400, "삭제된 세션입니다.")

    if opts.is_shared is not None:
        s.is_shared = bool(opts.is_shared)
    if opts.share_includes_audio is not None:
        s.share_includes_audio = bool(opts.share_includes_audio)
    if opts.share_includes_video is not None:
        s.share_includes_video = bool(opts.share_includes_video)
    if opts.share_includes_resume is not None:
        s.share_includes_resume = bool(opts.share_includes_resume)
    if opts.list_on_board is not None:
        s.list_on_board = bool(opts.list_on_board)

    # is_shared 가 꺼지면 세부 토글 + 게시판 등록도 모두 해제
    if not s.is_shared:
        s.share_includes_audio  = False
        s.share_includes_video  = False
        s.share_includes_resume = False
        s.list_on_board         = False

    db.commit()
    db.refresh(s)
    return ShareOptions(
        is_shared=s.is_shared,
        share_includes_audio=s.share_includes_audio,
        share_includes_video=s.share_includes_video,
        share_includes_resume=s.share_includes_resume,
        list_on_board=s.list_on_board,
    )


# ============================================================
# 세션 제목 수정
# ============================================================

@router.put("/{public_code}/title")
def rename_session(
    public_code: str,
    body: SessionTitleBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 — 면접 기록 제목 수정. null/빈 문자열이면 제목 제거."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if s.is_deleted:
        raise HTTPException(400, "삭제된 세션입니다.")

    new_title = (body.title or "").strip()
    s.title = new_title[:200] if new_title else None
    db.commit()
    return {"ok": True, "title": s.title}


# ============================================================
# 질문 만족도 평점
# ============================================================

class QuestionRating(BaseModel):
    rating: Optional[int]   # 1~5 or None (취소)


@router.put("/{public_code}/questions/{q_idx}/rating")
def rate_question(
    public_code: str,
    q_idx: int,
    body: QuestionRating,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인 — 질문 만족도 1~5 별점. null 보내면 취소."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if s.is_deleted:
        raise HTTPException(400, "삭제된 세션입니다.")

    q = next((qq for qq in s.questions if qq.order_no == q_idx), None)
    if not q:
        raise HTTPException(404, "질문을 찾을 수 없습니다.")

    r = body.rating
    if r is not None:
        if not isinstance(r, int) or r < 1 or r > 5:
            raise HTTPException(400, "평점은 1~5 사이의 정수여야 합니다.")
    q.user_satisfaction = r
    db.commit()
    return {"ok": True, "rating": r}


# ============================================================
# 소프트 삭제 / 복원
# ============================================================

@router.delete("/{public_code}")
def delete_session(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """본인/admin — 소프트 삭제 (데이터는 남고 접근만 차단)."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if s.is_deleted:
        return {"ok": True, "already_deleted": True}
    s.is_deleted = True
    s.deleted_at = datetime.utcnow()
    # 공유도 함께 끔
    s.is_shared = False
    db.commit()
    return {"ok": True, "deleted_at": s.deleted_at.isoformat()}


@router.post("/{public_code}/restore")
def restore_session(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """admin 만 — 소프트 삭제 해제."""
    if current_user.role != "admin":
        raise HTTPException(403, "관리자만 복원 가능합니다.")
    s = _get_session_or_404(db, public_code)
    s.is_deleted = False
    s.deleted_at = None
    db.commit()
    return {"ok": True}


# ============================================================
# 파일 다운로드 — 권한 체크 포함
# ============================================================

def _can_access_audio(s: InterviewSession, user: Optional[User]) -> bool:
    if _can_view_as_owner_or_admin(s, user):
        return True
    return bool(s.is_shared and s.share_includes_audio and not s.is_deleted)


def _can_access_resume(s: InterviewSession, user: Optional[User]) -> bool:
    if _can_view_as_owner_or_admin(s, user):
        return True
    return bool(s.is_shared and s.share_includes_resume and not s.is_deleted)


def _can_access_video(s: InterviewSession, user: Optional[User]) -> bool:
    if _can_view_as_owner_or_admin(s, user):
        return True
    return bool(s.is_shared and s.share_includes_video and not s.is_deleted)


@router.get("/{public_code}/audio/{q_idx}")
def download_audio(
    public_code: str,
    q_idx: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """질문별 음성 파일 다운로드 (1-based q_idx)."""
    s = _get_session_or_404(db, public_code)
    if s.is_deleted and (not current_user or current_user.role != "admin"):
        raise HTTPException(404, "삭제된 세션입니다.")
    if not _can_access_audio(s, current_user):
        raise HTTPException(403, "이 음성은 공유되지 않았습니다.")

    # questions 중 order_no == q_idx 의 audio_path 사용
    q = next((qq for qq in s.questions if qq.order_no == q_idx), None)
    if not q or not q.answer or not q.answer.audio_path:
        raise HTTPException(404, "음성 파일이 없습니다.")
    p = Path(q.answer.audio_path)
    if not p.exists():
        raise HTTPException(404, "파일이 디스크에 존재하지 않습니다.")
    return FileResponse(p, filename=p.name)


@router.get("/{public_code}/video/{q_idx}")
def get_video_url(
    public_code: str,
    q_idx: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """
    질문별 영상 다운로드/재생 — Supabase Storage 의 서명 URL 발급.

    응답: {"url": "https://...signed...", "expires_in": 3600}
    """
    s = _get_session_or_404(db, public_code)
    if s.is_deleted and (not current_user or current_user.role != "admin"):
        raise HTTPException(404, "삭제된 세션입니다.")
    if not _can_access_video(s, current_user):
        raise HTTPException(403, "이 영상은 공유되지 않았습니다.")

    # questions[q_idx].answer.video_clip
    q = next((qq for qq in s.questions if qq.order_no == q_idx), None)
    if not q or not q.answer:
        raise HTTPException(404, "답변 데이터가 없습니다.")
    vclip = q.answer.video_clip
    if not vclip or not vclip.video_path:
        raise HTTPException(404, "영상이 없습니다.")

    try:
        from app.services.storage import resolve_for_download
        url = resolve_for_download(vclip.video_path, expires_in=3600)
        if not url:
            raise HTTPException(500, "영상 URL 생성 실패")
        return {"url": url, "expires_in": 3600}
    except Exception as e:
        raise HTTPException(500, f"영상 URL 생성 실패: {e}")


@router.get("/{public_code}/resume")
def download_resume_text(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """이력서 텍스트 다운로드 (txt)."""
    s = _get_session_or_404(db, public_code)
    if s.is_deleted and (not current_user or current_user.role != "admin"):
        raise HTTPException(404, "삭제된 세션입니다.")
    if not _can_access_resume(s, current_user):
        raise HTTPException(403, "이력서는 공유되지 않았습니다.")
    if not s.resume or not s.resume.content_text:
        raise HTTPException(404, "이력서 데이터가 없습니다.")

    # 메모리에 만든 텍스트를 그대로 응답
    from fastapi.responses import Response
    content = s.resume.content_text.encode("utf-8")
    filename = s.resume.filename or "resume.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================
# 심층 분석 — gpt-5.5 기반 깊은 markdown 보고서
# ============================================================

@router.get("/{public_code}/deep-analysis")
def get_deep_analysis(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """저장된 심층 분석 결과 조회. 아직 없으면 available=False."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if not s.deep_analysis_md:
        return {
            "available": False,
            "public_code": s.public_code,
        }
    return {
        "available":  True,
        "public_code": s.public_code,
        "markdown":   s.deep_analysis_md,
        "model":      s.deep_analysis_model,
        "generated_at": s.deep_analysis_at.isoformat() if s.deep_analysis_at else None,
    }


@router.post("/{public_code}/deep-analysis")
def run_deep_analysis(
    public_code: str,
    force: bool = Query(False, description="이미 있어도 재생성"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """심층 분석 실행. 이미 있으면 그대로 반환 (force=true 면 재생성)."""
    s = _get_session_or_404(db, public_code)
    _ensure_owner_or_admin(s, current_user)
    if s.is_deleted:
        raise HTTPException(400, "삭제된 세션입니다.")

    if s.deep_analysis_md and not force:
        return {
            "available":    True,
            "public_code":  s.public_code,
            "markdown":     s.deep_analysis_md,
            "model":        s.deep_analysis_model,
            "generated_at": s.deep_analysis_at.isoformat() if s.deep_analysis_at else None,
            "cached":       True,
        }

    payload = _build_full_detail(s)
    payload["title"]        = s.title
    payload["public_code"]  = s.public_code
    payload["final_score_100"]   = s.final_score_100
    payload["content_score_80"]  = s.content_score_80
    payload["nonverbal_score_20"]= s.nonverbal_score_20

    try:
        from app.analysis.deep_analysis import generate_deep_analysis, DEEP_MODEL_DEFAULT
        result = generate_deep_analysis(payload, model=DEEP_MODEL_DEFAULT)
    except Exception as e:
        raise HTTPException(500, f"심층 분석 생성 실패: {e}")

    s.deep_analysis_md    = result["markdown"]
    s.deep_analysis_model = result["model"]
    s.deep_analysis_at    = datetime.utcnow()
    db.commit()

    return {
        "available":    True,
        "public_code":  s.public_code,
        "markdown":     s.deep_analysis_md,
        "model":        s.deep_analysis_model,
        "generated_at": s.deep_analysis_at.isoformat(),
        "cached":       False,
    }


# ============================================================
# 영구 삭제 — 관리자만, 이미 소프트 삭제된 세션에 한해 완전 제거
# ============================================================

@router.delete("/{public_code}/permanent")
def hard_delete_session(
    public_code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """관리자 전용 — 데이터베이스 + 음성/영상/이력서 파일까지 완전 제거.

    안전장치: **이미 소프트 삭제(is_deleted=True)** 된 세션만 가능.
    완전 제거되는 것:
      - DB: questions / answers / evaluations / nonverbal_metrics / video_clips
            / interview_sessions row (cascade)
      - Supabase Storage: 질문별 video_path
      - 로컬: sessions/{public_code}/  디렉토리 전체 (음성 wav/webm)
      - DB: 이 세션의 resume_id 가 다른 세션에서 사용되지 않으면 resume row 도 삭제
    """
    if current_user.role != "admin":
        raise HTTPException(403, "관리자만 영구 삭제할 수 있습니다.")

    s = _get_session_or_404(db, public_code)
    if not s.is_deleted:
        raise HTTPException(
            400,
            "먼저 소프트 삭제(휴지통으로 이동) 후 영구 삭제할 수 있습니다.",
        )

    removed = {
        "videos_deleted":   0,
        "videos_failed":    0,
        "audio_dir_removed": False,
        "resume_removed":   False,
    }

    # 1) Supabase Storage 영상 파일 삭제
    try:
        from app.services.storage import delete_video, parse_uri
        for q in s.questions:
            if q.answer and q.answer.video_clip and q.answer.video_clip.video_path:
                scheme, _ = parse_uri(q.answer.video_clip.video_path)
                if scheme == "supabase":
                    ok = False
                    try:
                        ok = delete_video(q.answer.video_clip.video_path)
                    except Exception:
                        ok = False
                    if ok: removed["videos_deleted"] += 1
                    else:  removed["videos_failed"]  += 1
    except Exception:
        # storage 모듈 자체가 막힌 환경 — 그래도 DB 정리는 계속
        pass

    # 2) 로컬 음성 디렉토리 삭제 (sessions/{public_code}/)
    if s.audio_dir:
        import shutil
        from pathlib import Path
        p = Path(s.audio_dir)
        if p.exists() and p.is_dir():
            try:
                shutil.rmtree(p)
                removed["audio_dir_removed"] = True
            except Exception:
                pass

    # 3) Resume 정리 — 같은 resume_id 를 쓰는 다른 세션이 없으면 삭제
    resume_id = s.resume_id
    db.delete(s)
    db.flush()

    if resume_id:
        from db import Resume
        other = (
            db.query(InterviewSession)
            .filter(InterviewSession.resume_id == resume_id)
            .first()
        )
        if not other:
            rz = db.query(Resume).filter(Resume.id == resume_id).first()
            if rz:
                db.delete(rz)
                removed["resume_removed"] = True

    db.commit()
    return {"ok": True, "public_code": public_code, **removed}
