"""
SQLAlchemy 2.x 모델 — 8개 테이블.

설계 원칙:
  - PK는 모두 UUID4 hex 문자열 (DB 무관, 분산 친화)
  - 외부 노출용 식별자는 public_code (URL-safe 10자)
  - 파일 본체는 외부 저장(로컬/S3), DB엔 경로만 저장
  - JSON 컬럼으로 가변 구조 데이터(평가 포인트, 강점/개선점 등) 보관
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime,
    Text, JSON, ForeignKey,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
)

from .utils import gen_uuid, gen_public_code


class Base(DeclarativeBase):
    pass


# ====================================================================
# 1. User — Google OAuth 계정
# ====================================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)

    # 공통 (이메일은 식별자로 항상 사용)
    email:   Mapped[str]            = mapped_column(String(255), unique=True, index=True)
    name:    Mapped[str]            = mapped_column(String(100), default="")
    picture: Mapped[Optional[str]]  = mapped_column(String(500), nullable=True)

    # 로컬 로그인 — auth_provider='local' 일 때 필수
    username:      Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, nullable=True, index=True,
    )
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone:         Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # 소셜 로그인 — auth_provider='google' 일 때 필수
    google_sub: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, nullable=True, index=True,
    )

    # 계정 메타
    auth_provider: Mapped[str] = mapped_column(
        String(20), default="local", index=True,
    )  # 'local' | 'google' | 'kakao'
    role: Mapped[str] = mapped_column(
        String(20), default="user", index=True,
    )  # 'user' | 'moderator' | 'admin'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 정지 만료 시각 — null 이면 영구 정지 (단, is_active=False 일 때만 의미).
    # 미래 시각이면 그때까지 정지, 과거 시각이면 자동 해제 (deps 에서 처리).
    suspended_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    resumes: Mapped[List["Resume"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )
    sessions: Mapped[List["InterviewSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
    )

    # 편의 속성 (role 체크용)
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_moderator(self) -> bool:
        return self.role in ("admin", "moderator")


# ====================================================================
# 2. Resume — 이력서/자기소개서
# ====================================================================
class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )

    filename: Mapped[str] = mapped_column(String(255))
    format: Mapped[str] = mapped_column(String(10))      # .txt / .docx / .pdf / .hwp / .hwpx
    content_text: Mapped[str] = mapped_column(Text)       # 추출된 텍스트
    storage_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 원본 파일 경로 (선택 — 다시 추출하거나 다운로드 제공 시)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="resumes")
    sessions: Mapped[List["InterviewSession"]] = relationship(back_populates="resume")


# ====================================================================
# 3. InterviewSession — 1회 면접 = 1 row
# ====================================================================
class InterviewSession(Base):
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    # 외부 공유용 짧은 ID. 항상 자동 생성.
    public_code: Mapped[str] = mapped_column(
        String(12), unique=True, index=True, default=gen_public_code,
    )

    # 사용자 지정 제목 (null 이면 UI 가 "면접" + 코드 로 자동 표시)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    resume_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True,
    )

    # 상태: in_progress | completed | failed
    status: Mapped[str] = mapped_column(String(20), default="in_progress")

    # 공유 설정
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    share_token: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, unique=True,
    )
    # 공유 시 노출 범위 (모두 false 가 기본 — Q&A 텍스트만 공개)
    share_includes_audio:  Mapped[bool] = mapped_column(Boolean, default=False)
    share_includes_video:  Mapped[bool] = mapped_column(Boolean, default=False)
    share_includes_resume: Mapped[bool] = mapped_column(Boolean, default=False)

    # 공유 게시판(/board) 노출 — is_shared 가 true 일 때만 의미 있음.
    list_on_board:         Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # 소프트 삭제 — 실제 데이터는 남고 접근만 차단
    is_deleted: Mapped[bool]                  = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[Optional[datetime]]    = mapped_column(DateTime, nullable=True)

    # 음성 파일 디렉토리 경로 (sessions/{public_code}/)
    audio_dir: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 답변 평가에 사용한 OpenAI 모델 — 관리자 화면에서 표시.
    model_used: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # 최종 점수 (finalize 후 채워짐)
    final_score_100:    Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    content_score_80:   Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nonverbal_score_20: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 심층 분석 — '심층 분석' 버튼이 눌리면 gpt-5.5 가 생성한 markdown 저장
    deep_analysis_md:    Mapped[Optional[str]]     = mapped_column(Text, nullable=True)
    deep_analysis_model: Mapped[Optional[str]]     = mapped_column(String(40), nullable=True)
    deep_analysis_at:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    started_at:   Mapped[datetime]            = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]]  = mapped_column(DateTime, nullable=True)
    created_at:   Mapped[datetime]            = mapped_column(DateTime, default=datetime.utcnow)

    user:     Mapped[User]              = relationship(back_populates="sessions")
    resume:   Mapped[Optional[Resume]]  = relationship(back_populates="sessions")
    questions: Mapped[List["Question"]] = relationship(
        back_populates="session", cascade="all, delete-orphan",
        order_by="Question.order_no",
    )
    nonverbal_metrics: Mapped[Optional["NonverbalMetrics"]] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan",
    )


# ====================================================================
# 4. Question — 질문별 메타 (id_str / text / intent / eval_points)
# ====================================================================
class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True,
    )

    order_no: Mapped[int] = mapped_column(Integer)          # 1, 2, 3, ...
    question_id_str: Mapped[str] = mapped_column(String(20))  # "C_INTRO", "P01" 등 (생성기 ID)

    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(Text, default="")
    evaluation_points: Mapped[list] = mapped_column(JSON, default=list)
    # ↑ ["측정 도구 사용했는가", "가설 검증 과정이 드러나는가", ...]

    # 사용자 만족도 (1~5, null = 미평가)
    user_satisfaction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[InterviewSession] = relationship(back_populates="questions")
    answer: Mapped[Optional["Answer"]] = relationship(
        back_populates="question", uselist=False, cascade="all, delete-orphan",
    )


# ====================================================================
# 5. Answer — 음성 + 변환된 텍스트
# ====================================================================
class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True, unique=True,
    )

    audio_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 로컬: 'sessions/{sid}/answer_1.wav'  →  S3로 옮기면 's3://bucket/.../answer_1.wav'
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    transcript: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question: Mapped[Question] = relationship(back_populates="answer")
    evaluation: Mapped[Optional["Evaluation"]] = relationship(
        back_populates="answer", uselist=False, cascade="all, delete-orphan",
    )
    video_clip: Mapped[Optional["VideoClip"]] = relationship(
        back_populates="answer", uselist=False, cascade="all, delete-orphan",
    )


# ====================================================================
# 6. Evaluation — 답변별 80점 평가 상세
# ====================================================================
class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    answer_id: Mapped[str] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"),
        index=True, unique=True,
    )

    content_score:   Mapped[float] = mapped_column(Float)    # 0~80
    common_subtotal: Mapped[float] = mapped_column(Float)    # 0~50
    custom_subtotal: Mapped[float] = mapped_column(Float)    # 0~30

    # 6개 일반 항목 점수
    common_scores: Mapped[dict] = mapped_column(JSON)
    # {question_understanding, answer_structure, resume_job_relevance, specificity, logic, conciseness}

    # 질문별 평가 포인트 점수 (각 0~5)
    custom_scores: Mapped[list] = mapped_column(JSON)
    # [{point: "...", score: 0~5}, ...]

    strengths:        Mapped[list] = mapped_column(JSON)
    improvements:     Mapped[list] = mapped_column(JSON)
    content_feedback: Mapped[str]  = mapped_column(Text, default="")
    sample_answer:    Mapped[str]  = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    answer: Mapped[Answer] = relationship(back_populates="evaluation")


# ====================================================================
# 7. VideoClip — 얼굴 인식 기반 영상 (답변별 or 세션별)
# ====================================================================
class VideoClip(Base):
    __tablename__ = "video_clips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)

    # 둘 중 하나만 채움 (질문별 or 세션 전체)
    answer_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("answers.id", ondelete="CASCADE"),
        nullable=True, unique=True,
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=True,
    )

    video_path:   Mapped[str]            = mapped_column(String(500))
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mime_type:    Mapped[str]            = mapped_column(String(50), default="video/webm")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    answer: Mapped[Optional[Answer]] = relationship(back_populates="video_clip")


# ====================================================================
# 8. NonverbalMetrics — 세션 단위 비언어 종합 (20점)
# ====================================================================
class NonverbalMetrics(Base):
    __tablename__ = "nonverbal_metrics"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=gen_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        unique=True,
    )

    score_20: Mapped[float] = mapped_column(Float)    # 0~20

    smile_score:   Mapped[float] = mapped_column(Float)   # 0~6
    focus_score:   Mapped[float] = mapped_column(Float)   # 0~6
    blink_score:   Mapped[float] = mapped_column(Float)   # 0~4
    posture_score: Mapped[float] = mapped_column(Float)   # 0~4

    smile_ratio:        Mapped[float] = mapped_column(Float)   # %
    focus_ratio:        Mapped[float] = mapped_column(Float)   # %
    blink_per_minute:   Mapped[float] = mapped_column(Float)
    avg_movement_px:    Mapped[float] = mapped_column(Float)

    duration_sec: Mapped[float] = mapped_column(Float)
    silent_sec:   Mapped[float] = mapped_column(Float)
    speak_sec:    Mapped[float] = mapped_column(Float)

    # 미래 확장용 raw dict (jitter, F0 std 등 추가 시)
    raw_metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[InterviewSession] = relationship(back_populates="nonverbal_metrics")
