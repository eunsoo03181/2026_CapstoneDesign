"""
FastAPI 백엔드 — 브라우저에서 면접 진행.

실행:
    uvicorn main:app --reload
    → http://localhost:8000

엔드포인트:
    GET  /                              → 웹 UI (static/index.html)
    POST /api/interview/start           → 이력서 업로드 + 질문 생성
    POST /api/interview/{sid}/answer/{q_idx}  → 음성 업로드 + 변환
    POST /api/interview/{sid}/finalize  → 평가 + 최종 점수
    GET  /api/interview/{sid}/result    → 저장된 결과 조회
    GET  /api/interview/{sid}/download/{filename} → 리포트 파일 다운로드

세션 저장은 메모리 dict (DB 도입 전 임시).
"""

import os
import json
import uuid
import shutil
import asyncio
from pathlib import Path
from typing import Optional, List

# ⚠️ .env 파일 자동 로드 — 다른 import 보다 먼저 (auth/database/openai_usage 등이 import 시점에
# os.getenv() 를 평가하므로, load_dotenv() 가 늦으면 모두 None 으로 읽힘).
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from app.questions.question_generator import (
    build_interview_questions,
    summarize_resume_async,
    generate_question_texts_async,
    enrich_questions_parallel,
    pick_common_questions,
    assemble_questions,
    INTRO_QUESTION_ID,
    CLOSING_QUESTION_ID,
)
from app.services.speech_to_text import transcribe_with_whisper, transcribe_with_whisper_words

# 빈/환각 답변 표시용 placeholder. 평가는 이 값을 만나면 LLM 호출 없이 0점 처리.
EMPTY_ANSWER_TEXT = "(내용 없음)"
from app.scoring.answer_evaluator import evaluate_session
from app.scoring.final_score import compute_final_score
from app.services.resume_loader import load_resume, SUPPORTED_EXTS
from app.analysis.report_writer import save_session_report

from db import init_db, get_db, User
from db.models import InterviewSession
from db.persist import persist_finalized_session
from db.utils import gen_public_code
from auth.google import register_google_oauth
from auth.deps import get_current_user
from routers.auth_routes import router as google_auth_router
from routers.local_auth_routes import router as local_auth_router
from routers.sessions_routes import router as sessions_router
from routers.admin_routes import router as admin_router
from routers.board_routes import router as board_router
from sqlalchemy.orm import Session
from fastapi import Depends


# ---------- 경로 ----------
BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
SESSIONS_DIR = BASE_DIR / "sessions"
UPLOADS_DIR = BASE_DIR / "uploads"

STATIC_DIR.mkdir(exist_ok=True)
SESSIONS_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)


# ---------- 환경변수 자가 점검 ----------
# .env 가 비어 있을 때 어디서 어떻게 실패하는지 알려주는 부팅 가드.
# 필수 키가 비면 즉시 RuntimeError, 선택 키는 콘솔 경고만 출력하고 계속 진행.

def _check_env_keys() -> None:
    def _missing(name: str) -> bool:
        return not (os.getenv(name) or "").strip()

    # 필수 — 없으면 핵심 기능(질문 생성·평가·STT) 자체가 동작 불가
    if _missing("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY 가 비어 있습니다. .env 파일의 OPENAI_API_KEY= 줄에 키를 채워 주세요.\n"
            "발급: https://platform.openai.com/api-keys"
        )

    warnings: list[str] = []

    # 권장 — 빈 값이면 임시 fallback 으로 동작 (개발 전용)
    sess = (os.getenv("SESSION_SECRET_KEY") or "").strip()
    if not sess or sess == "dev-secret-change-me":
        warnings.append(
            "SESSION_SECRET_KEY 가 비어 있거나 기본값입니다 → 임시 시크릿으로 동작. "
            "운영 배포 전에 반드시 교체:  python -c \"import secrets; print(secrets.token_hex(32))\""
        )

    # 선택 — 각 기능별 비활성 안내
    if _missing("DATABASE_URL"):
        warnings.append("DATABASE_URL 미설정 → SQLite (interview.db) 로 폴백.")
    if _missing("SUPABASE_URL") or _missing("SUPABASE_SERVICE_KEY"):
        warnings.append("SUPABASE_URL / SUPABASE_SERVICE_KEY 미설정 → 영상 업로드 비활성 (음성·점수만 저장).")
    if _missing("GOOGLE_CLIENT_ID") or _missing("GOOGLE_CLIENT_SECRET"):
        warnings.append("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 미설정 → 구글 로그인 버튼 비활성 (로컬 로그인만).")
    if _missing("SMTP_USER") or _missing("SMTP_APP_PASSWORD"):
        warnings.append("SMTP_USER / SMTP_APP_PASSWORD 미설정 → 인증 메일을 콘솔에 출력 (개발 fallback).")

    if warnings:
        print("\n⚠️  환경변수 경고:")
        for w in warnings:
            print(f"   - {w}")
        print()


_check_env_keys()


# ---------- 앱 ----------
app = FastAPI(title="Signal Catch - AI 모의면접")


# ─── 422 디버그 핸들러 — 어떤 필드가 거부됐는지 콘솔 + 클라이언트 모두 보이게 ─────
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request, exc: RequestValidationError):
    print(f"[422] {request.method} {request.url.path}")
    errors_summary = []
    for err in exc.errors():
        loc = " → ".join(str(x) for x in (err.get("loc") or []))
        # 'input' 은 너무 길거나 바이너리일 수 있어 repr 후 잘라냄
        try:
            input_repr = repr(err.get("input"))
            if len(input_repr) > 120:
                input_repr = input_repr[:117] + "..."
        except Exception:
            input_repr = "<unrepr>"
        print(f"      ✗ {loc}: {err.get('msg')}  (type={err.get('type')}, input={input_repr})")
        errors_summary.append(f"{loc or '필드'}: {err.get('msg')}")
    # detail 을 단일 문자열로 — 클라이언트가 err.detail 만 출력해도 [object Object] 안 뜨게
    detail_text = " / ".join(errors_summary) or "요청 형식이 올바르지 않습니다."
    return JSONResponse(
        status_code=422,
        content={
            "detail": detail_text,    # 사람이 읽을 수 있는 한 줄
            "errors": exc.errors(),    # 디버깅용 원본 배열
        },
    )

# 세션 쿠키 미들웨어 (OAuth 콜백 / 로그인 상태 유지)
# SESSION_SECRET_KEY 가 비어 있으면 프로세스 수명 동안만 유효한 임시 시크릿 발급.
# (재시작 시 모든 로그인 세션 무효화 — 개발 전용 동작. _check_env_keys 에서 경고 출력됨.)
_session_secret = (os.getenv("SESSION_SECRET_KEY") or "").strip()
if not _session_secret:
    import secrets as _secrets
    _session_secret = _secrets.token_hex(32)

app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    same_site="lax",
    https_only=False,  # 배포 시 True
)

# DB 테이블 생성 (개발용 — SQLite 일 때만 자동 생성, PostgreSQL/Supabase 는 SQL Editor 로 1회 실행했음)
from db.database import DATABASE_URL as _DB_URL
if _DB_URL.startswith("sqlite"):
    init_db()

# Google OAuth 클라이언트 등록
register_google_oauth()

# 라우터 등록
app.include_router(google_auth_router)   # /auth/google/login, /callback, /logout, /me
app.include_router(local_auth_router)    # /auth/signup, /login, /change-password
app.include_router(sessions_router)      # /api/sessions/*
app.include_router(admin_router)         # /api/admin/* (admin only)
app.include_router(board_router)         # /api/board/* — 공유 게시판

# 메모리 세션 저장 (DB 통합 전 임시 — 다음 단계에서 InterviewSession 테이블로 교체)
SESSIONS: dict = {}  # sid -> { resume_text, questions, answers[], result? }


# 사용 가능한 모델 화이트리스트 (사용자 입력 검증용)
ALLOWED_MODELS = {"gpt-4o-mini", "gpt-4o", "gpt-5.4"}
DEFAULT_MODEL = "gpt-4o-mini"
NONVERBAL_FEEDBACK_MODEL = "gpt-4o-mini"   # 얼굴 인식 피드백은 항상 고정


def normalize_model(m: Optional[str]) -> str:
    return m if m in ALLOWED_MODELS else DEFAULT_MODEL


# 압박 질문 카테고리별 한도.
#   - additive: per-category 1/1/2, 단 사용자 총 질문 < 5 이면 1/1/1
#   - focused : 사실상 무제한 (큰 정수). 체인 깊이는 별도(이미 maybe-followup 에서 강제)
#   - off     : 0
_PRESSURE_UNLIMITED = 10_000
def _compute_pressure_cap(mode: str, kind: str, total_questions: int) -> int:
    if mode == "off":
        return 0
    if mode == "focused":
        return _PRESSURE_UNLIMITED
    # additive
    under_5 = total_questions < 5
    if kind == "common":
        return 1
    if kind == "personalized":
        return 1
    if kind == "followup":
        return 1 if under_5 else 2
    return 0


# ---------- 응답 스키마 ----------
class QuestionDict(BaseModel):
    question_id: str
    question: str
    intent: str = ""
    evaluation_points: List[str] = []


class StartResponse(BaseModel):
    session_id: str
    questions: List[QuestionDict]
    n_questions: int


class AnswerResponse(BaseModel):
    question_index: int
    transcript: str


# ---------- 엔드포인트 ----------

# 모든 HTML 페이지 응답에 캐시 차단 헤더를 박아, HTML 변경이 곧바로 반영되도록.
# (정적 자산은 /static 마운트가 별도로 ETag 처리)
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _page(filename: str) -> FileResponse:
    """no-cache 헤더가 박힌 HTML 응답 헬퍼."""
    return FileResponse(STATIC_DIR / filename, headers=_NO_CACHE_HEADERS)


@app.get("/favicon.ico")
def favicon_ico():
    """레거시 /favicon.ico 요청 → 같은 SVG 로 응답."""
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.svg")
def favicon_svg():
    """SVG favicon — 모던 브라우저는 link rel=icon 으로 직접 요청."""
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
def index(request: Request):
    """루트 → 로그인 안 되어 있으면 /login 으로 리다이렉트."""
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)
    return _page("index.html")


@app.get("/login")
def login_page(request: Request):
    """로그인/회원가입 페이지. 이미 로그인 되어 있으면 / 로."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=302)
    return _page("login.html")


@app.get("/history")
def history_page(request: Request):
    """본인 면접 기록 리스트 페이지."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("history.html")


@app.get("/session/{public_code}")
def session_detail_page(public_code: str, request: Request):
    """본인/admin 전용 세션 상세 페이지."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("session_detail.html")


@app.get("/analysis/{public_code}")
def analysis_page(public_code: str, request: Request):
    """본인/admin 전용 면접 분석 페이지 (시각화·통찰)."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("analysis.html")


@app.get("/share/{public_code}")
def share_page(public_code: str):
    """공유 페이지 — 누구나 접근 가능 (실제 권한은 API 에서 체크)."""
    return _page("share.html")


@app.get("/board")
def board_page(request: Request):
    """공유 게시판 — 로그인 사용자만, 게시판에 등록된 면접을 둘러봄."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("board.html")


@app.get("/finalizing/{sid}")
def finalizing_page(sid: str, request: Request):
    """면접 직후 — 평가 진행 중 대기 화면. JS 가 finalize 호출 후 /result 로 이동."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("finalizing.html")


@app.get("/result/{sid}")
def result_page(sid: str, request: Request):
    """면접 직후 결과 화면 — finalize 가 끝난 직후 표시."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("result.html")


@app.get("/admin")
def admin_page(request: Request):
    """관리자 대시보드 — 실제 권한 체크는 /api/admin/* 가 담당."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("admin.html")


@app.get("/admin/users/{user_id}")
def admin_user_detail_page(user_id: str, request: Request):
    """관리자 — 특정 사용자 상세 페이지."""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login", status_code=302)
    return _page("admin_user.html")


# ============================================================
# 면접 시작 — 스트리밍 (진행도 + 분할 파이프라인)
# ============================================================

def _sse(data: dict) -> str:
    """SSE 메시지 포맷 (한 줄 JSON + 빈 줄)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/interview/start-stream")
async def start_interview_stream(
    resume_text: Optional[str] = Form(None),
    resume_file: Optional[UploadFile] = File(None),
    n_personalized: int = Form(5, ge=0, le=15),
    n_common: int = Form(2, ge=0, le=10),
    model: str = Form(DEFAULT_MODEL),
    cam_mode: str = Form("realtime"),     # 'realtime' | 'record' | 'off'
    interview_mode: str = Form("practice"),   # 'practice' | 'realistic' — realistic 은 중도 이탈 시 자동 폐기
    # ─── 관리자 전용 디버그 옵션 (admin role 이 아니면 무시) ──────────────────
    debug_topic_bias:      str   = Form("balanced"),  # 'balanced' | 'tech' | 'personality' | 'experience' | 'pressure'
    debug_difficulty_bias: str   = Form("balanced"),  # 'balanced' | 'basic' | 'advanced'
    debug_style_bias:      str   = Form("balanced"),  # 'balanced' | 'structured' | 'conversational'
    debug_force_keywords:  str   = Form(""),          # 콤마 구분 (예: 'Redis, Kafka, A/B 테스트')
    # Form 단계 ge/le 제거 — 슬라이더 step 부동소수 오차로 검증 실패하는 케이스 회피.
    # 실제 clamp 는 SESSIONS 저장 시 0.1~3.0 으로 적용 (아래 _debug 블록).
    debug_score_leniency:  float = Form(1.0),
    debug_role_specific:   bool  = Form(False),       # 기업/직무 맞춤 질문 추가 생성
    enable_followups: bool = Form(False), # 꼬리질문 사용 여부 (체크박스)
    # 압박 면접 옵션
    pressure_mode: str = Form("off"),      # 'off' | 'additive' | 'focused'
    criticism_level: int = Form(5, ge=0, le=10),
    # 회사·직무 리서치 (선택)
    company_name: Optional[str] = Form(None),
    job_title:    Optional[str] = Form(None),
    company_text: Optional[str] = Form(None),  # 사용자가 붙여넣은 JD/회사 설명
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # ----- 이메일 인증 체크 (로컬 가입 한정) -----
    # Google/admin/moderator 는 자동 통과. 인증 안 한 로컬 가입자는 면접 생성 차단.
    if not bool(getattr(current_user, "email_verified", False)):
        raise HTTPException(
            status_code=403,
            detail=(
                "이메일 인증을 먼저 완료해주세요. 가입 시 받은 메일의 링크를 클릭하거나, "
                "메일이 안 왔다면 우측 상단에서 재발송을 요청하세요."
            ),
        )

    # ----- Credit 체크 + 차감 (admin/mod 면 차감 없이 통과) -----
    # 면접 생성 시도 시점에서 1 credit 소비. 부족하면 402 — 클라이언트가 안내.
    from app.scoring.credit_ops import consume_credit, InsufficientCreditError
    try:
        consume_credit(db, current_user, reason="interview_create")
    except InsufficientCreditError as e:
        raise HTTPException(
            status_code=402,   # Payment Required — credit 부족 신호로 활용
            detail=f"credit이 부족해 면접을 생성할 수 없어요. (잔액 {e.current_balance}개) 관리자에게 credit 부여를 요청하세요.",
        )
    """
    스트리밍 면접 시작.

    이벤트 시퀀스 (text/event-stream):
      1) status   — 진행 메시지 + progress(0~100)
      2) questions — 질문 텍스트 목록 + session_id  (사용자가 첫 질문 답변 시작 가능)
      3) enriched — intent / evaluation_points 까지 채워진 완성형 질문 목록
      4) done

    Phase 3: 자소서가 길면 사전 요약 → 입력 토큰 절감
    Phase 4: 질문 텍스트 먼저 → 사용자 답변 시작 → 메타데이터 백그라운드 보강
    """
    # ----- 0) 이력서 텍스트 / 파일 수신 -----
    resume_filename: Optional[str] = None
    resume_format: Optional[str] = None

    if resume_file is not None and resume_file.filename:
        ext = os.path.splitext(resume_file.filename)[1].lower()
        if ext not in SUPPORTED_EXTS:
            raise HTTPException(400, f"지원하지 않는 형식: {ext}")
        tmp_path = UPLOADS_DIR / f"{uuid.uuid4().hex}{ext}"
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(resume_file.file, f)
        try:
            resume_text = load_resume(str(tmp_path))
            resume_filename = resume_file.filename
            resume_format = ext
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    if not resume_text or not resume_text.strip():
        raise HTTPException(400, "이력서가 비어있습니다.")

    # 캡처용 (제너레이터 안에서 사용)
    user_id = current_user.id
    captured_resume = resume_text
    captured_filename = resume_filename
    captured_format = resume_format
    chosen_model = normalize_model(model)
    chosen_cam_mode = cam_mode if cam_mode in ("realtime", "record", "off") else "realtime"
    chosen_pressure_mode = pressure_mode if pressure_mode in ("off", "additive", "focused") else "off"
    chosen_criticism = max(0, min(10, int(criticism_level)))
    captured_company_name = (company_name or "").strip()
    captured_job_title    = (job_title or "").strip()
    captured_company_text = (company_text or "").strip()

    async def event_generator():
        try:
            # 1) 자소서 분석
            yield _sse({"type": "status", "progress": 5,
                        "message": "이력서를 살펴보고 있어요"})

            summary = await summarize_resume_async(captured_resume, model=chosen_model)
            was_summarized = (summary != captured_resume)

            if was_summarized:
                yield _sse({"type": "status", "progress": 25,
                            "message": "핵심 내용을 정리했어요"})

            # 1.5) 회사·직무 정보가 제공되면 요약 생성 + summary 에 합치기
            company_summary_json = None
            company_block = ""
            if captured_company_name or captured_job_title or captured_company_text:
                # 사용자가 텍스트를 안 붙여넣은 경우엔 회사명·직무명만으로 자동 리서치
                use_auto_research = not captured_company_text and (
                    captured_company_name or captured_job_title
                )
                yield _sse({"type": "status", "progress": 30,
                            "message": ("회사·직무 정보를 자동으로 찾고 있어요"
                                        if use_auto_research
                                        else "회사·직무 정보를 정리하고 있어요")})
                try:
                    from app.company.company_research import (
                        summarize_company_job_from_text_async,
                        research_company_from_name_async,
                        format_company_block,
                        merge_candidate_and_company,
                    )
                    if use_auto_research:
                        company_summary_json = await research_company_from_name_async(
                            model=chosen_model,
                            company_name=captured_company_name,
                            job_title=captured_job_title,
                            candidate_summary=summary,
                        )
                    else:
                        company_summary_json = await summarize_company_job_from_text_async(
                            model=chosen_model,
                            company_name=captured_company_name,
                            job_title=captured_job_title,
                            pasted_text=captured_company_text,
                            candidate_summary=summary,
                        )
                    if company_summary_json:
                        company_block = format_company_block(company_summary_json)
                        summary = merge_candidate_and_company(summary, company_block)
                except Exception as _e:
                    # 회사 리서치 실패해도 면접은 진행
                    company_summary_json = None
                    company_block = ""

            # 2) 질문 텍스트 빠르게 추출
            yield _sse({"type": "status", "progress": 40,
                        "message": "맞춤 질문을 추출하고 있어요"})

            # ─── 디버그 편향 instruction (admin 한정, 비-admin 은 모두 'balanced' 라 빈 문자열) ───
            _debug = {
                "topic_bias":      "balanced",
                "difficulty_bias": "balanced",
                "style_bias":      "balanced",
                "force_keywords":  [],
                "role_specific":   False,
            }
            if current_user.role == "admin":
                _debug = {
                    "topic_bias":      debug_topic_bias if debug_topic_bias in ("tech","personality","experience","pressure") else "balanced",
                    "difficulty_bias": debug_difficulty_bias if debug_difficulty_bias in ("basic","advanced") else "balanced",
                    "style_bias":      debug_style_bias if debug_style_bias in ("structured","conversational") else "balanced",
                    "force_keywords":  [k.strip() for k in (debug_force_keywords or "").split(",") if k.strip()][:10],
                    "role_specific":   bool(debug_role_specific),
                }
            bias_lines = []
            if _debug["topic_bias"] != "balanced":
                # 'tech' 는 회사·직무가 입력돼 있으면 그 회사의 실제 직무 맥락을 직접 박아넣음
                if _debug["topic_bias"] == "tech" and (captured_company_name or captured_job_title):
                    _cn = captured_company_name or "지원 회사"
                    _jt = captured_job_title    or "지원 직무"
                    bias_lines.append(
                        f"질문을 **{_cn} 의 {_jt} 가 실무에서 실제로 마주칠 문제·기술 스택·업무 흐름** 중심으로 만들 것. "
                        f"일반 CS 이론(자료구조·OS·네트워크) 보다 그 회사·직무의 **구체적 시나리오** 를 우선시. "
                        f"가능하면 회사명({_cn})·직무({_jt})·실제 사용 기술/서비스 키워드를 질문 본문에 자연스럽게 포함."
                    )
                else:
                    _topic_map = {
                        "tech":        "주제 비중을 지원 직무에 필요한 기술·실무 도구·일하는 방식 쪽으로 기울일 것.",
                        "personality": "주제 비중을 인성·태도·가치관·갈등 해결 쪽으로 기울일 것.",
                        "experience":  "주제 비중을 과거 경험·프로젝트 사례 쪽으로 기울일 것.",
                        "pressure":    "질문 톤을 도전적 검증 쪽으로 — 사실 확인·근거 추궁을 자연스럽게 섞을 것.",
                    }
                    bias_lines.append(_topic_map[_debug["topic_bias"]])
            if _debug["difficulty_bias"] == "basic":
                bias_lines.append("난이도 비중을 기초~중급 쪽으로 기울일 것 (개념·정의 확인 중심).")
            elif _debug["difficulty_bias"] == "advanced":
                bias_lines.append("난이도 비중을 심화 쪽으로 기울일 것 (트레이드오프·실패 케이스·내부 메커니즘).")
            if _debug["style_bias"] == "structured":
                bias_lines.append(
                    "답변에 결론·근거·사례·마무리 같은 구조가 자연스럽게 드러나도록 유도하되, "
                    "**모든 질문이 'STAR 형식으로 설명해주세요' 같은 명시 형식을 강요하지 말 것** — "
                    "그런 요구는 전체 질문 중 1개 정도로만 제한. 나머지는 평범한 질문 문장 안에서 사례·근거를 묻는 정도."
                )
            elif _debug["style_bias"] == "conversational":
                bias_lines.append(
                    "캐주얼하고 자연스러운 대화 흐름의 짧은 질문. "
                    "같은 인사·도입(예: '~에 대해 말씀해주실 수 있나요')을 매번 반복하지 말 것."
                )
            if _debug["force_keywords"]:
                kws = ", ".join(_debug["force_keywords"])
                bias_lines.append(f"다음 키워드 중 일부를 질문 본문에 자연스럽게 포함시킬 것: {kws}.")
            bias_block = ""
            if bias_lines:
                bias_block = (
                    "\n\n[관리자 디버그 지시 — 편향 적용]\n"
                    + "\n".join(f"- {l}" for l in bias_lines)
                    + "\n\n[공통 제약]\n"
                    "- 매 질문이 같은 접두어·문장 패턴을 반복하지 말고 어휘·구조를 다양화하라.\n"
                    "- 편향은 비중 조절일 뿐, 모든 질문이 동일한 형식·주제로 도배되지 않도록 자연스럽게 섞어라."
                )

            question_texts = await generate_question_texts_async(
                summary + bias_block, n=n_personalized, model=chosen_model,
            )

            # 공통 질문 (정적 — 즉시 사용 가능)
            common = pick_common_questions(n_common)

            # role_specific (디버그 옵션) — 회사명/직무 입력됐을 때만 의미 있음
            role_specific_stubs: list = []
            if _debug["role_specific"] and (captured_company_name or captured_job_title):
                try:
                    from app.questions.question_generator import generate_role_specific_questions_async
                    rs_qs = await generate_role_specific_questions_async(
                        company_name=captured_company_name,
                        job_title=captured_job_title,
                        job_description="",
                        company_keywords="",
                        major="",
                        resume_summary=summary[:1500],
                        num_questions=min(3, n_personalized),
                        model=chosen_model,
                    )
                    for i, rs in enumerate(rs_qs):
                        role_specific_stubs.append({
                            "question_id": f"RS{i+1:02d}",
                            "question":    rs.get("question") or "",
                            "intent":      rs.get("intent") or rs.get("job_relevance") or "",
                            "evaluation_points": rs.get("evaluation_points") or [],
                            "is_role_specific": True,
                        })
                except Exception as _e:
                    role_specific_stubs = []

            # 임시 형태 (intent / eval_points 비어있음)
            personalized_stub = [
                {
                    "question_id": f"P{i+1:02d}",
                    "question": q,
                    "intent": "",
                    "evaluation_points": [],
                }
                for i, q in enumerate(question_texts)
            ]
            personalized_stub.extend(role_specific_stubs)
            preliminary = assemble_questions(personalized_stub, common)

            # 세션 등록 (메모리)
            sid = gen_public_code(10)
            SESSIONS[sid] = {
                "user_id":         user_id,
                "resume_text":     captured_resume,
                "resume_filename": captured_filename,
                "resume_format":   captured_format,
                "questions":       preliminary,
                "answers":         [None] * len(preliminary),
                "stt_words":       [None] * len(preliminary),     # 답변별 [{word,start,end}, ...] (음성 답변만)
                "video_paths":     [None] * len(preliminary),   # 질문별 영상 URI (supabase:...)
                "model":           chosen_model,
                "cam_mode":        chosen_cam_mode,
                "nonverbal_metrics": None,    # 종료 시 클라이언트에서 전달
                # 꼬리질문 — enable_followups 면 전체에서 최대 2 * len(preliminary) 회 까지,
                # 한 체인 안에서는 최대 2 단계까지 추가 연결.
                # (사용자가 정한 본 질문 개수의 2배 만큼 꼬리질문 허용)
                "enable_followups":     enable_followups,
                "followup_count_used":  0,
                "followup_count_max":   (2 * len(preliminary)) if enable_followups else 0,
                # ---------- 압박 면접 ----------
                # mode:  'off'      → 압박 비활성
                #        'additive' → 일반 질문 안에 압박 일부 섞기 (per-category 1/1/2, 총합<5 면 1/1/1)
                #        'focused'  → 압박 위주 진행 (per-category 사실상 무제한, 체인 깊이만 유효)
                "pressure_mode":               chosen_pressure_mode,
                "criticism_level":             chosen_criticism,
                "pressure_common_used":        0,
                "pressure_personalized_used":  0,
                "pressure_followup_used":      0,
                "pressure_common_max":         _compute_pressure_cap(chosen_pressure_mode, "common",       len(preliminary)),
                "pressure_personalized_max":   _compute_pressure_cap(chosen_pressure_mode, "personalized", len(preliminary)),
                "pressure_followup_max":       _compute_pressure_cap(chosen_pressure_mode, "followup",     len(preliminary)),
                # 사용자가 '면접 중단' 으로 중도 종료했는지
                "aborted":                     False,
                # 회사·직무 컨텍스트 (선택)
                "company_name":         captured_company_name,
                "job_title":            captured_job_title,
                "company_job_summary":  company_summary_json,
                "company_block":        company_block,
                # research_mode — 'auto' (회사명만으로 LLM 리서치),
                #                  'pasted' (사용자가 JD 텍스트 붙여넣음),
                #                  'none' (회사 정보 미입력)
                "company_research_mode": (
                    "auto" if (captured_company_name or captured_job_title) and not captured_company_text
                    else ("pasted" if captured_company_text else "none")
                ),
                # interview_mode — 'practice' (기본, 재개 가능) | 'realistic' (중도 이탈 시 폐기)
                "interview_mode": ("realistic" if interview_mode == "realistic" else "practice"),
                # ─── 디버그 옵션 (admin 한정 — 일반 사용자가 폼 위변조로 보내도 무시) ────
                "debug_options": (
                    {
                        "topic_bias":      debug_topic_bias if debug_topic_bias in ("tech","personality","experience","pressure") else "balanced",
                        "difficulty_bias": debug_difficulty_bias if debug_difficulty_bias in ("basic","advanced") else "balanced",
                        "style_bias":      debug_style_bias if debug_style_bias in ("structured","conversational") else "balanced",
                        "force_keywords":  [k.strip() for k in (debug_force_keywords or "").split(",") if k.strip()][:10],
                        "score_leniency":  max(0.1, min(3.0, float(debug_score_leniency))),
                        "role_specific":   bool(debug_role_specific),
                    }
                    if (current_user.role == "admin") else
                    {
                        "topic_bias": "balanced", "difficulty_bias": "balanced",
                        "style_bias": "balanced", "force_keywords": [],
                        "score_leniency": 1.0, "role_specific": False,
                    }
                ),
            }

            yield _sse({"type": "status", "progress": 70,
                        "message": "거의 다 됐어요"})

            # 3) 질문 텍스트 전달 — 클라이언트는 여기서부터 면접 진행 가능
            yield _sse({
                "type": "questions",
                "session_id": sid,
                "questions": preliminary,
                "n_questions": len(preliminary),
                "progress": 85,
                "message": "면접을 시작할 준비가 됐어요",
            })

            # 4) 백그라운드 보강 — intent / evaluation_points 채우기
            enriched = await enrich_questions_parallel(question_texts, summary, model=chosen_model)

            # 4.5) 압박 질문 통합 — 모드/한도에 따라 일부 슬롯을 압박 변형으로 교체
            if chosen_pressure_mode != "off":
                from app.questions.pressure_generator import (
                    generate_pressure_question_async,
                    generate_company_job_pressure_question_async,
                )
                p_pers_max = SESSIONS[sid].get("pressure_personalized_max", 0)
                n_to_make = (
                    len(enriched) if chosen_pressure_mode == "focused"
                    else min(p_pers_max, len(enriched))
                )
                if n_to_make > 0:
                    yield _sse({"type": "status", "progress": 90,
                                "message": "압박 질문을 만들고 있어요"})
                    # 회사·직무 정보가 있으면 회사 기반 압박 질문 우선 시도
                    pressures = []
                    if company_summary_json:
                        co_press = await generate_company_job_pressure_question_async(
                            model=chosen_model,
                            candidate_summary=summary,
                            company_job_summary=company_summary_json,
                            previous_questions=[q.get("question","") for q in enriched],
                            n=min(n_to_make, 3),   # 회사 기반은 한 번에 3개까지
                            criticism_level=chosen_criticism,
                        )
                        # 회사 기반 결과를 generic 압박 포맷에 맞춤
                        for c in co_press:
                            pressures.append({
                                "question":          c["question"],
                                "pressure_type":     c.get("pressure_type",""),
                                "resume_basis":      c.get("candidate_basis",""),
                                "target_competency": c.get("detected_gap",""),
                                "evaluation_points": [c.get("evaluation_focus")] if c.get("evaluation_focus") else [],
                            })
                    # 부족분은 일반(resume_based) 압박으로 채움
                    if len(pressures) < n_to_make:
                        more = await generate_pressure_question_async(
                            model=chosen_model,
                            candidate_summary=summary,
                            previous_questions=[q.get("question","") for q in enriched]
                                              + [p["question"] for p in pressures],
                            n=n_to_make - len(pressures),
                            criticism_level=chosen_criticism,
                        )
                        pressures.extend(more)
                    if pressures:
                        # focused: 앞부분부터 모조리 압박으로 / additive: 무작위 N 슬롯만
                        import random as _r
                        if chosen_pressure_mode == "focused":
                            target_slots = list(range(min(len(pressures), len(enriched))))
                        else:
                            target_slots = _r.sample(
                                range(len(enriched)),
                                min(len(pressures), len(enriched), p_pers_max),
                            )
                        used = 0
                        for slot in target_slots:
                            if used >= len(pressures):
                                break
                            p = pressures[used]
                            used += 1
                            enriched[slot] = {
                                "question_id":       f"PP{slot+1:02d}",
                                "question":          p["question"],
                                "intent":            f"[압박] {p.get('target_competency') or p.get('pressure_type','')}".strip(),
                                "evaluation_points": p.get("evaluation_points") or [],
                                "is_pressure":       True,
                                "pressure_type":     p.get("pressure_type",""),
                                "criticism_level":   chosen_criticism,
                            }
                        SESSIONS[sid]["pressure_personalized_used"] = used

            final_questions = assemble_questions(enriched, common)

            # SESSIONS 의 question 메타 업데이트 (답변 인덱스 보존)
            SESSIONS[sid]["questions"] = final_questions

            yield _sse({
                "type": "enriched",
                "questions": final_questions,
                "progress": 100,
                "message": "준비 완료!",
            })
            yield _sse({"type": "done"})

        except Exception as e:
            yield _sse({"type": "error", "message": f"문제가 발생했어요: {e}"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/interview/start", response_model=StartResponse)
async def start_interview(
    resume_text: Optional[str] = Form(None),
    resume_file: Optional[UploadFile] = File(None),
    n_personalized: int = Form(2, ge=0, le=15),
    n_common: int = Form(1, ge=0, le=10),
    current_user: User = Depends(get_current_user),
):
    """이력서 텍스트 또는 파일을 받아 질문 생성."""
    resume_filename: Optional[str] = None
    resume_format:   Optional[str] = None

    # 1) 이력서 텍스트 확보 (파일 우선, 없으면 직접 입력)
    if resume_file is not None and resume_file.filename:
        ext = os.path.splitext(resume_file.filename)[1].lower()
        if ext not in SUPPORTED_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"지원하지 않는 형식: {ext}. 지원: {SUPPORTED_EXTS}",
            )
        tmp_path = UPLOADS_DIR / f"{uuid.uuid4().hex}{ext}"
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(resume_file.file, f)
        try:
            resume_text = load_resume(str(tmp_path))
            resume_filename = resume_file.filename
            resume_format = ext
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    if not resume_text or not resume_text.strip():
        raise HTTPException(
            status_code=400,
            detail="이력서 텍스트가 비어있습니다 (resume_text 또는 resume_file 필요).",
        )

    # 2) 질문 생성
    questions = build_interview_questions(
        resume_text,
        n_personalized=n_personalized,
        n_common=n_common,
    )

    # 3) 세션 등록 — public_code 를 곧장 session_id 로 사용 (URL/공유와 통일)
    sid = gen_public_code(10)
    SESSIONS[sid] = {
        "user_id":         current_user.id,
        "resume_text":     resume_text,
        "resume_filename": resume_filename,
        "resume_format":   resume_format,
        "questions":       questions,
        "answers":         [None] * len(questions),
        "stt_words":       [None] * len(questions),
    }
    return StartResponse(
        session_id=sid,
        questions=questions,
        n_questions=len(questions),
    )


@app.post("/api/interview/{sid}/answer/{q_idx}", response_model=AnswerResponse)
async def submit_answer(
    sid: str,
    q_idx: int,
    audio: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """질문별 음성 업로드 → Whisper 로 텍스트 변환."""
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")
    if not (0 <= q_idx < len(sess["questions"])):
        raise HTTPException(status_code=400, detail="질문 인덱스 범위 오류.")

    # 음성 파일 저장 (브라우저 MediaRecorder 가 webm/ogg 로 보냄)
    sess_dir = SESSIONS_DIR / sid
    sess_dir.mkdir(exist_ok=True)
    src_name = audio.filename or "audio.webm"
    ext = os.path.splitext(src_name)[1].lower() or ".webm"
    audio_path = sess_dir / f"answer_{q_idx + 1}{ext}"

    with open(audio_path, "wb") as f:
        shutil.copyfileobj(audio.file, f)

    # Whisper STT — 단어별 timestamp 까지 받아 음성 비언어 분석에 활용
    # (clean_transcript 가 무음/환각 답변을 이미 빈 문자열로 정규화)
    stt = transcribe_with_whisper_words(str(audio_path))
    transcript = (stt.get("text") or "").strip()
    words = stt.get("words", []) or []
    # 빈/환각 답변은 통일된 placeholder 로 — UI 와 평가가 같은 값을 봄
    stored_transcript = transcript if transcript else EMPTY_ANSWER_TEXT
    sess["answers"][q_idx] = stored_transcript
    # stt_words 자리 부족시 안전 보강 (followup 으로 length 가 늘어났을 수 있음)
    if "stt_words" not in sess:
        sess["stt_words"] = [None] * len(sess["answers"])
    while len(sess["stt_words"]) < len(sess["answers"]):
        sess["stt_words"].append(None)
    sess["stt_words"][q_idx] = words

    # 클라이언트가 받는 transcript 도 정규화된 값
    return AnswerResponse(question_index=q_idx, transcript=stored_transcript)


# ============================================================
# 텍스트 답변 (녹음 대신 직접 입력) + 비언어 metrics 수신
# ============================================================

class TextAnswerBody(BaseModel):
    transcript: str


@app.post("/api/interview/{sid}/answer-text/{q_idx}", response_model=AnswerResponse)
def submit_text_answer(
    sid: str,
    q_idx: int,
    body: TextAnswerBody,
    current_user: User = Depends(get_current_user),
):
    """녹음 대신 텍스트 입력 — Whisper 없이 즉시 저장."""
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")
    if not (0 <= q_idx < len(sess["questions"])):
        raise HTTPException(status_code=400, detail="질문 인덱스 범위 오류.")

    txt = (body.transcript or "").strip()
    if not txt:
        raise HTTPException(status_code=400, detail="답변 텍스트가 비어있습니다.")
    # 사용자가 의도적으로 (내용 없음) 같은 placeholder 를 직접 입력했어도 동일 처리
    if txt == EMPTY_ANSWER_TEXT:
        txt = EMPTY_ANSWER_TEXT  # noop, 명시
    sess["answers"][q_idx] = txt
    # 텍스트 직접 입력은 음성 데이터 없음 → stt_words 는 빈 리스트로 마킹
    if "stt_words" not in sess:
        sess["stt_words"] = [None] * len(sess["answers"])
    while len(sess["stt_words"]) < len(sess["answers"]):
        sess["stt_words"].append(None)
    sess["stt_words"][q_idx] = []
    return AnswerResponse(question_index=q_idx, transcript=txt)


# ============================================================
# 꼬리질문 — 직전 답변을 바탕으로 추가 질문 1개 생성 후 questions 리스트에 삽입
# ============================================================

@app.post("/api/interview/{sid}/maybe-followup/{q_idx}")
async def maybe_followup(
    sid: str,
    q_idx: int,
    current_user: User = Depends(get_current_user),
):
    """
    꼬리질문 생성 시도.

    하드 한도 (서버 강제):
      - sess['enable_followups'] = False              → null
      - followup_count_used >= max (2× 본 질문 개수)   → null
      - parent 의 chain_depth >= 2                    → null
      - 마지막 질문 + 공통 질문(C_*)이면                → null
      - 답변이 비어있으면                              → null

    소프트 한도 (LLM 판정):
      - DECISION 프롬프트로 'should_ask_follow_up' 판정 → false 면 null
      - 모드:
         * parent.is_followup=False, 직전 element 가 꼬리질문 → STRICT
         * parent.is_followup=True (체인 2단계 생성)         → LOOSE
         * 그 외                                           → NORMAL

    성공 시: questions / answers / video_paths 에 q_idx+1 위치에 새 항목 삽입.
    """
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")
    if not (0 <= q_idx < len(sess["questions"])):
        raise HTTPException(status_code=400, detail="질문 인덱스 범위 오류.")

    if not sess.get("enable_followups"):
        return {"question": None, "reason": "disabled"}

    used = int(sess.get("followup_count_used", 0))
    cap  = int(sess.get("followup_count_max", 0))
    if used >= cap:
        return {"question": None, "reason": "quota_exhausted"}

    questions_list = sess["questions"]
    parent = questions_list[q_idx]
    parent_depth = int(parent.get("chain_depth", 0))
    if parent_depth >= 2:
        return {"question": None, "reason": "chain_limit"}

    # 마지막 질문 + 공통 질문(C_*)이면 꼬리질문을 만들지 않음
    is_last_in_interview = (q_idx == len(questions_list) - 1)
    is_common = str(parent.get("question_id", "")).startswith("C_")
    if is_last_in_interview and is_common:
        return {"question": None, "reason": "last_question_is_common"}

    answer = sess["answers"][q_idx]
    if not answer or not str(answer).strip():
        return {"question": None, "reason": "no_answer"}

    # 결정 모드 산출
    parent_is_followup = bool(parent.get("is_followup", False))
    prev_is_followup = (
        q_idx > 0 and bool(questions_list[q_idx - 1].get("is_followup", False))
    )
    if parent_is_followup:
        strictness_mode = "loose"     # 체인 2단계 생성
    elif prev_is_followup:
        strictness_mode = "strict"    # 직전 본 질문에서도 꼬리질문이 있었음
    else:
        strictness_mode = "normal"

    previous_questions = [
        str(q.get("question", ""))
        for q in questions_list[: q_idx + 1]
        if q.get("question")
    ]
    chosen_model = normalize_model(sess.get("model"))

    # 1) 결정 — 꼬리질문이 필요한가?
    try:
        from app.questions.followup_generator import (
            decide_followup_async,
            generate_followup_async,
        )
        decision = await decide_followup_async(
            model=chosen_model,
            original_question=parent.get("question", ""),
            question_intent=parent.get("intent", ""),
            evaluation_points=parent.get("evaluation_points", []) or [],
            candidate_answer=str(answer),
            candidate_summary=sess.get("resume_text", "") or "",
            previous_questions=previous_questions,
            followup_count_used=used,
            followup_count_max=cap,
            strictness_mode=strictness_mode,
        )
    except Exception as e:
        return {"question": None, "reason": f"decision_error: {e}"}

    if not decision:
        # 판정 실패 — 안전상 skip
        return {"question": None, "reason": "decision_unavailable"}
    if not decision.get("should_ask_follow_up"):
        return {
            "question": None,
            "reason": "decision_skip",
            "decision": decision.get("raw"),
            "strictness_mode": strictness_mode,
        }

    # 1.5) 압박 꼬리질문 분기 결정
    pressure_mode = sess.get("pressure_mode", "off")
    p_fu_used = int(sess.get("pressure_followup_used", 0))
    p_fu_max  = int(sess.get("pressure_followup_max", 0))
    crit_lvl  = int(sess.get("criticism_level", 5))
    is_pressure_fu = False
    if pressure_mode != "off" and p_fu_used < p_fu_max:
        try:
            from app.questions.pressure_generator import (
                decide_pressure_question_async,
                generate_pressure_followup_question_async,
            )
            p_dec = await decide_pressure_question_async(
                model=chosen_model,
                mode="answer_based_followup",
                candidate_summary=sess.get("resume_text", "") or "",
                previous_questions=previous_questions,
                pressure_used=p_fu_used,
                pressure_max=p_fu_max,
                criticism_level=crit_lvl,
                original_question=parent.get("question", ""),
                question_intent=parent.get("intent", ""),
                evaluation_points=parent.get("evaluation_points", []) or [],
                candidate_answer=str(answer),
                pressure_followup_used=p_fu_used,
                pressure_followup_max=p_fu_max,
            )
            if p_dec and p_dec.get("should_generate_pressure_question"):
                # 답변 기반 압박 꼬리질문 생성 — 직전 답변의 용어/누락/근거 부족을 검증
                p_gen = await generate_pressure_followup_question_async(
                    model=chosen_model,
                    candidate_summary=sess.get("resume_text", "") or "",
                    original_question=parent.get("question", ""),
                    question_intent=parent.get("intent", ""),
                    evaluation_points=parent.get("evaluation_points", []) or [],
                    candidate_answer=str(answer),
                    previous_questions=previous_questions,
                    criticism_level=crit_lvl,
                    job_title=sess.get("job_title", "") or "",
                )
                if p_gen:
                    fu = {
                        "question":            p_gen["question"],
                        "focus":               p_gen.get("followup_type", ""),
                        "target_competency":   p_gen.get("evaluation_focus", ""),
                        "missing_evidence":    p_gen.get("detected_gap", ""),
                        "why_this_question":  f"[압박 꼬리질문] {p_gen.get('followup_type','')}".strip(' []'),
                        "evaluation_points":   [p_gen.get("evaluation_focus")] if p_gen.get("evaluation_focus") else [],
                        "detected_terms":      p_gen.get("detected_terms") or [],
                        "basis_from_answer":   p_gen.get("basis_from_answer", ""),
                    }
                    is_pressure_fu = True
                    sess["pressure_followup_used"] = p_fu_used + 1
        except Exception:
            # 압박 분기 실패 시 일반 꼬리질문으로 폴백
            is_pressure_fu = False

    # 2) 일반 꼬리질문 생성 (압박이 아닌 경우만)
    if not is_pressure_fu:
        try:
            fu = await generate_followup_async(
                model=chosen_model,
                original_question=parent.get("question", ""),
                question_intent=parent.get("intent", ""),
                evaluation_points=parent.get("evaluation_points", []) or [],
                candidate_answer=str(answer),
                candidate_summary=sess.get("resume_text", "") or "",
                previous_questions=previous_questions,
            )
        except Exception as e:
            return {"question": None, "reason": f"generate_error: {e}"}

        if not fu:
            return {"question": None, "reason": "model_returned_empty"}

    new_idx = q_idx + 1
    new_q = {
        "question_id":       f"{'PFU' if is_pressure_fu else 'FU'}{used + 1:02d}",
        "question":          fu["question"],
        "intent":            fu.get("why_this_question") or f"꼬리질문 — {fu.get('focus','')}".strip(' —'),
        "evaluation_points": fu.get("evaluation_points") or [],
        "is_followup":       True,
        "is_pressure":       is_pressure_fu,
        "chain_depth":       parent_depth + 1,
        "parent_question_id": parent.get("question_id"),
        "focus":             fu.get("focus", ""),
        "target_competency": fu.get("target_competency", ""),
    }

    sess["questions"].insert(new_idx, new_q)
    sess["answers"].insert(new_idx, None)
    if isinstance(sess.get("video_paths"), list):
        sess["video_paths"].insert(new_idx, None)
    if isinstance(sess.get("stt_words"), list):
        sess["stt_words"].insert(new_idx, None)

    sess["followup_count_used"] = used + 1

    return {
        "question":             new_q,
        "inserted_at":          new_idx,
        "followup_count_used":  sess["followup_count_used"],
        "followup_count_max":   cap,
    }


@app.post("/api/interview/{sid}/video/{q_idx}")
async def submit_video(
    sid: str,
    q_idx: int,
    video: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """
    질문별 영상 파일을 Supabase Storage 의 interview-video 버킷에 업로드.
    cam_mode='off' 인 세션에선 사용 X.

    반환: {video_uri: "supabase:interview-video/{sid}/answer_{q+1}.webm"}
    """
    if sid not in SESSIONS:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(403, "본인 세션이 아닙니다.")
    if sess.get("cam_mode") == "off":
        raise HTTPException(400, "이 세션은 카메라 비사용 모드입니다.")
    if not (0 <= q_idx < len(sess["questions"])):
        raise HTTPException(400, "질문 인덱스 범위 오류.")

    src_name = video.filename or "video.webm"
    ext = os.path.splitext(src_name)[1].lstrip(".").lower() or "webm"
    content_type = video.content_type or "video/webm"

    file_bytes = await video.read()

    try:
        from app.services.storage import upload_video
        uri = upload_video(
            file_bytes=file_bytes,
            session_code=sid,
            q_idx=q_idx + 1,        # 1-based
            content_type=content_type,
            extension=ext,
        )
    except Exception as e:
        raise HTTPException(500, f"영상 업로드 실패: {e}")

    sess["video_paths"][q_idx] = uri
    return {"video_uri": uri, "question_index": q_idx}


@app.post("/api/interview/{sid}/nonverbal")
def submit_nonverbal_metrics(
    sid: str,
    metrics: dict,
    current_user: User = Depends(get_current_user),
):
    """클라이언트(MediaPipe.js)에서 계산된 비언어 metrics 를 받아 세션에 저장."""
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")
    sess["nonverbal_metrics"] = metrics
    return {"ok": True}


# ============================================================
# 디버깅 — 관리자가 기존 면접의 질문을 그대로 복사해 다시 면접 보기
# ============================================================
@app.post("/api/admin/sessions/{public_code}/replay")
def replay_session_as_admin(
    public_code: str,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """관리자 전용 — 원본 면접의 질문 리스트를 그대로 복사해 새 면접 세션을 메모리에 만든다.

    용도: 모범 답안이 실제로 만점을 받는지 검증 등 디버깅.
    - 답변/녹음/영상은 새로 받음 (원본 데이터 복사 X)
    - 카메라는 off 로 고정 (디버깅 마찰 최소화)
    - 꼬리질문/압박 생성은 비활성 (질문 셋 고정)
    """
    if me.role != "admin":
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")

    # 원본 세션 조회 (삭제됐어도 admin 은 열람 가능)
    src = (
        db.query(InterviewSession)
          .filter(InterviewSession.public_code == public_code)
          .first()
    )
    if not src:
        raise HTTPException(404, "원본 면접 세션을 찾을 수 없습니다.")
    if not src.questions:
        raise HTTPException(400, "원본 세션에 질문이 없습니다.")

    # 질문 dict 리스트로 변환 (order_no 오름차순)
    questions: List[dict] = []
    for q in sorted(src.questions, key=lambda x: x.order_no):
        qid = q.question_id_str or f"Q{q.order_no}"
        questions.append({
            "question_id":       qid,
            "question":          q.text,
            "intent":            q.intent or "",
            "evaluation_points": q.evaluation_points or [],
            # 원본이 꼬리/압박이었으면 표시 유지 (UI 가 배지로 노출)
            "is_followup":       qid.startswith(("FU", "PFU")),
            "is_pressure":       qid.startswith(("PP", "PFU")),
        })

    resume_text     = src.resume.content_text if src.resume else ""
    resume_filename = src.resume.filename     if src.resume else None
    resume_format   = src.resume.format       if src.resume else None

    new_sid = gen_public_code(10)
    SESSIONS[new_sid] = {
        "user_id":         me.id,                 # 새 면접의 소유자는 현재 관리자
        "resume_text":     resume_text,
        "resume_filename": resume_filename,
        "resume_format":   resume_format,
        "questions":       questions,
        "answers":         [None] * len(questions),
        "stt_words":       [None] * len(questions),
        "video_paths":     [None] * len(questions),
        "model":           normalize_model(src.model_used),
        "cam_mode":        "off",                 # 디버깅 — 카메라 사용 안 함
        "nonverbal_metrics": None,
        # 질문 셋 고정 — 추가 생성 비활성
        "enable_followups":     False,
        "followup_count_used":  0,
        "followup_count_max":   0,
        "pressure_mode":               "off",
        "criticism_level":             0,
        "pressure_common_used":        0,
        "pressure_personalized_used":  0,
        "pressure_followup_used":      0,
        "pressure_common_max":         0,
        "pressure_personalized_max":   0,
        "pressure_followup_max":       0,
        "aborted":                     False,
        # 회사 컨텍스트는 복사하지 않음 (재평가 노이즈 줄이기)
        "company_name":         "",
        "job_title":            "",
        "company_job_summary":  None,
        "company_block":        "",
        # 추적 메타
        "replay_of_public_code": public_code,
        "replay_started_by":    me.id,
    }

    return {
        "ok":         True,
        "sid":        new_sid,
        "n_questions": len(questions),
        "redirect":   f"/?replay={new_sid}",
    }


# ============================================================
# 면접 중단 — 압박 위주 모드에서 사용자가 중간에 끝낼 때
# ============================================================
@app.post("/api/interview/{sid}/abort")
def abort_interview(
    sid: str,
    q_idx: int = Form(0),    # 마지막으로 답변한 질문 인덱스 (0-based)
    current_user: User = Depends(get_current_user),
):
    """
    사용자가 '면접 중단' 을 누르면, 미답변 질문을 모두 잘라내고
    답변한 분량만 남긴 채로 평가가 가능한 상태로 만든다.

    클라이언트는 이 호출 직후 그대로 /finalizing/{sid} 로 이동.
    실제 채점은 기존 finalize 흐름이 처리.
    """
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")

    # 실전 모드 — 중도 이탈은 무조건 폐기 (finalize/DB 저장 X)
    if sess.get("interview_mode") == "realistic":
        SESSIONS.pop(sid, None)
        return {
            "ok": True,
            "discarded": True,
            "reason": "realistic_mode_abort",
            "redirect": "/",
        }

    # q_idx 까지 답변한 분량만 남기고 나머지 잘라냄
    answers = sess.get("answers") or []
    keep_until = max(0, min(int(q_idx) + 1, len(answers)))
    # 마지막으로 본 인덱스가 미답변이면 그 항목은 떼어냄
    if keep_until > 0 and not (answers[keep_until - 1] and str(answers[keep_until - 1]).strip()):
        keep_until -= 1

    if keep_until <= 0:
        raise HTTPException(400, "최소 한 개 질문은 답변되어야 중단할 수 있습니다.")

    sess["questions"]   = sess["questions"][:keep_until]
    sess["answers"]     = answers[:keep_until]
    if isinstance(sess.get("video_paths"), list):
        sess["video_paths"] = sess["video_paths"][:keep_until]
    if isinstance(sess.get("stt_words"), list):
        sess["stt_words"] = sess["stt_words"][:keep_until]
    sess["aborted"]     = True

    return {
        "ok":       True,
        "kept":     keep_until,
        "redirect": f"/finalizing/{sid}",
    }


@app.post("/api/interview/{sid}/finalize")
def finalize(
    sid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """모든 답변 평가 + 100점 종합 + 리포트 저장 + DB 영속화.

    멱등 — 이미 한 번 finalize 됐다면 동일한 결과를 그대로 반환.
    """
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션 없음.")
    sess = SESSIONS[sid]
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="본인 세션이 아닙니다.")
    # 이미 처리됐으면 그대로 반환 (대기 페이지 재방문/새로고침 대응)
    if "result" in sess:
        return sess["result"]

    # 질문은 dict 그대로 + 답변 텍스트 → evaluator 가 intent/eval_points 자동 추출
    qa_pairs = [
        (q, a if (a and a.strip()) else EMPTY_ANSWER_TEXT)
        for q, a in zip(sess["questions"], sess["answers"])
    ]

    # 답변 채점 (80점) — 사용자가 선택한 모델 사용
    chosen_model = normalize_model(sess.get("model"))
    evaluation = evaluate_session(
        qa_pairs,
        resume_summary=sess["resume_text"],
        model=chosen_model,
    )

    # 시각 비언어 — 클라이언트에서 받은 metrics 사용 (cam_mode='off' 면 None)
    nonverbal_metrics = sess.get("nonverbal_metrics")

    # 음성 비언어 — 답변별 STT words 로 계산해 세션 평균 산출 (10점)
    voice_eval = None
    try:
        from app.nonverbal.voice_nonverbal_eval import (
            evaluate_voice_nonverbal_from_transcript,
            aggregate_voice_evals,
        )
        stt_words = sess.get("stt_words") or [None] * len(sess["answers"])
        per_q_voice = []
        for ans_text, words in zip(sess["answers"], stt_words):
            if words is None:
                # 답변 없음 / 옛 세션
                per_q_voice.append(None)
                continue
            per_q_voice.append(
                evaluate_voice_nonverbal_from_transcript(ans_text or "", words)
            )
        sess["voice_per_question"] = per_q_voice
        voice_eval = aggregate_voice_evals(per_q_voice)
    except Exception as e:
        voice_eval = {"ok": False, "reason": f"voice_eval_failed: {e}",
                      "voice_nonverbal_total": 0, "max_score": 10}
        sess["voice_per_question"] = []

    # 답변 일관성 검증 (점수 미반영, 표시용)
    consistency_checks = []
    try:
        from app.questions.consistency_checker import check_consistency_for_session
        consistency_qa = [
            {
                "question": q.get("text") or q.get("question") or "",
                "answer":   a or "",
                "intent":   q.get("intent") or "",
            }
            for q, a in zip(sess["questions"], sess["answers"])
        ]
        consistency_checks = check_consistency_for_session(
            consistency_qa,
            resume_summary=sess.get("resume_text") or "",
            cover_letter_summary=sess.get("cover_letter_text") or "",
            model="gpt-4o-mini",
        )
    except Exception as e:
        consistency_checks = [
            {"level": "없음", "summary": f"(검증 실패: {e})", "issues": [],
             "question_index": i}
            for i, _ in enumerate(sess["answers"])
        ]
    sess["consistency_checks"] = consistency_checks

    # 100점 종합 — 언어 80 + 시각 10 + 음성 10
    # 디버그 — admin 한정 leniency 적용 (SESSIONS 에 저장된 값 사용. 일반 사용자는 항상 1.0)
    _leniency = float((sess.get("debug_options") or {}).get("score_leniency") or 1.0)
    final = compute_final_score(
        evaluation["summary"], nonverbal_metrics, voice_eval,
        leniency_factor=_leniency,
    )

    # 비언어 피드백 (얼굴 인식 평가는 항상 4o-mini)
    nonverbal_feedback = None
    if nonverbal_metrics and nonverbal_metrics.get("ok"):
        try:
            from app.nonverbal.nonverbal_feedback import generate_nonverbal_feedback
            nonverbal_feedback = generate_nonverbal_feedback(
                nonverbal_metrics, model=NONVERBAL_FEEDBACK_MODEL,
            )
        except Exception as e:
            nonverbal_feedback = {"error": str(e)}

    # 영상 업로드 여부 (질문 인덱스별) — 결과 화면 다운로드 버튼용
    video_uploaded = [bool(v) for v in (sess.get("video_paths") or [])]

    # 회사·직무 리서치 결과 — 사용자가 회사 검색이 실제로 작동했는지 확인용
    company_research = {
        "company_name":     sess.get("company_name") or "",
        "job_title":        sess.get("job_title") or "",
        "research_mode":    sess.get("company_research_mode") or "none",
        "company_job_summary": sess.get("company_job_summary"),  # LLM 출력 JSON
        "company_block":    sess.get("company_block") or "",     # 프롬프트용 포맷 텍스트
    }

    result = {
        "questions": sess["questions"],
        "qa_pairs": qa_pairs,
        "content_evaluation": evaluation,
        "nonverbal_metrics": nonverbal_metrics,
        "nonverbal_feedback": nonverbal_feedback,
        # 음성 비언어 & 일관성 — 신규
        "voice_eval": voice_eval,
        "voice_per_question": sess.get("voice_per_question") or [],
        "consistency_checks": consistency_checks,
        # 회사·직무 리서치 결과
        "company_research": company_research,
        "cam_mode": sess.get("cam_mode", "realtime"),
        "model_used": chosen_model,
        "video_uploaded": video_uploaded,
        "final": final,
    }

    # 리포트 저장 (txt + json) — wav 는 sessions/{sid}/ 에 이미 있음
    out_dir = save_session_report(
        result,
        resume_text=sess["resume_text"],
        base_dir=str(SESSIONS_DIR),
        move_wav_files=False,
    )
    result["saved_to"] = out_dir
    sess["result"] = result

    # DB 영속화 — 면접 기록/공유/삭제 기능에 필요
    audio_dir = str(SESSIONS_DIR / sid) if (SESSIONS_DIR / sid).exists() else None
    try:
        db_session = persist_finalized_session(
            db,
            user=current_user,
            public_code=sid,
            resume_text=sess["resume_text"],
            resume_filename=sess.get("resume_filename"),
            resume_format=sess.get("resume_format"),
            audio_dir=audio_dir,
            questions_data=sess["questions"],
            answers_data=qa_pairs,
            evaluation=evaluation,
            final=final,
            nonverbal_metrics=nonverbal_metrics,
            nonverbal_feedback=nonverbal_feedback,
            voice_eval=voice_eval,
            voice_per_question=sess.get("voice_per_question") or [],
            consistency_checks=consistency_checks,
            company_research=company_research,
            video_paths=sess.get("video_paths"),
            model_used=chosen_model,
        )
        result["db_session_id"] = db_session.id
        result["public_code"] = db_session.public_code
    except Exception as e:
        # DB persist 실패해도 결과 반환은 가능 (다음 단계에서 강화)
        result["persist_error"] = str(e)

    return result


@app.get("/api/interview/{sid}/state")
def get_interview_state(
    sid: str,
    current_user: User = Depends(get_current_user),
):
    """
    진행 중인 면접 세션의 상태를 조회 — 새로고침/탭 종료 후 재개 용도.

    반환:
      - exists=False         → 서버 메모리에 없음 (재시작됐거나 만료)
      - completed=True       → 이미 finalize 끝났음 → /result/{sid} 로 안내
      - 그 외 → questions / answered_indices / first_unanswered / 설정값 일체
    """
    if sid not in SESSIONS:
        return {"exists": False}
    sess = SESSIONS[sid]
    # 본인 세션이 아니면 존재하지 않는 것처럼 응답
    if sess.get("user_id") != current_user.id and current_user.role != "admin":
        return {"exists": False}
    # 이미 결과까지 나왔으면 재개할 게 아님 — 결과 페이지로 안내
    if "result" in sess:
        return {"exists": False, "completed": True}
    # 실전 모드 — state 조회가 들어왔다는 건 사용자가 이탈했다 돌아온 것.
    # 정책상 실전 모드는 재개 불가 → 세션 즉시 폐기 + exists=False 응답.
    if sess.get("interview_mode") == "realistic":
        SESSIONS.pop(sid, None)
        return {"exists": False, "discarded": True, "reason": "realistic_mode_no_resume"}

    answers = sess.get("answers", []) or []
    answered_indices = [i for i, a in enumerate(answers) if a]
    first_unanswered = next(
        (i for i, a in enumerate(answers) if not a),
        len(answers),
    )
    return {
        "exists":               True,
        "questions":            sess.get("questions", []),
        "answered_indices":     answered_indices,
        "first_unanswered":     first_unanswered,
        "n_questions":          len(sess.get("questions", [])),
        "model":                sess.get("model", DEFAULT_MODEL),
        "cam_mode":             sess.get("cam_mode", "realtime"),
        "enable_followups":     bool(sess.get("enable_followups", False)),
        "followup_count_used":  int(sess.get("followup_count_used", 0)),
        "followup_count_max":   int(sess.get("followup_count_max", 0)),
    }


@app.get("/api/interview/{sid}/result")
def get_result(sid: str):
    if sid not in SESSIONS:
        raise HTTPException(status_code=404, detail="세션 없음.")
    if "result" not in SESSIONS[sid]:
        raise HTTPException(status_code=404, detail="아직 finalize 되지 않음.")
    return SESSIONS[sid]["result"]


@app.get("/api/interview/{sid}/download/{filename}")
def download_file(sid: str, filename: str):
    """저장된 report.txt / data.json 등 다운로드."""
    if sid not in SESSIONS or "result" not in SESSIONS[sid]:
        raise HTTPException(status_code=404, detail="결과 없음.")
    saved_dir = SESSIONS[sid]["result"].get("saved_to")
    if not saved_dir:
        raise HTTPException(status_code=404, detail="저장 경로 없음.")
    file_path = Path(saved_dir) / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"파일 없음: {filename}")
    return FileResponse(file_path, filename=filename)


# 정적 파일 (CSS, JS 등 — index.html 외)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "active_sessions": len(SESSIONS)}


# ─── 관리자 — 세션 final 점수 재계산 (leniency 적용된 옛 세션 복구용) ─────
class RecomputeBody(BaseModel):
    leniency: float = 1.0
    dry_run: bool = False


@app.post("/api/admin/sessions/{public_code}/recompute")
def recompute_session_final_score(
    public_code: str,
    body: RecomputeBody,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """관리자 전용 — DB 의 final/content/nonverbal 점수를 leniency 기준으로 재계산.

    - leniency=1.0 (기본): 원본 복구. 답변별 raw evaluations 평균 + nonverbal raw 사용.
    - dry_run=True: DB 저장 없이 새 값만 반환 (UI 미리보기용).
    - evaluations 의 content_score 는 항상 raw 라 ÷계수 보다 정확.
    """
    if me.role != "admin":
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")

    leniency = max(0.1, min(3.0, float(body.leniency or 1.0)))

    s = (
        db.query(InterviewSession)
          .filter(InterviewSession.public_code == public_code)
          .first()
    )
    if not s:
        raise HTTPException(404, "세션을 찾을 수 없습니다.")

    # 1) content — evaluations.content_score 의 평균 (원본, leniency 영향 안 받음)
    content_scores = []
    for q in s.questions:
        if q.answer and q.answer.evaluation:
            content_scores.append(float(q.answer.evaluation.content_score or 0))
    content_avg = (sum(content_scores) / len(content_scores)) if content_scores else 0.0

    # 2) nonverbal raw — NonverbalMetrics + raw_metrics_json
    nv = s.nonverbal_metrics
    raw = (nv.raw_metrics_json if nv else None) or {}
    visual_score_20_raw = float(nv.score_20) if nv else 0.0
    visual_ok = bool(visual_score_20_raw > 0 or (isinstance(raw, dict) and raw.get("ok")))
    nonverbal_metrics_arg = {"ok": True, "score_20": visual_score_20_raw} if visual_ok else None
    voice_eval_arg = raw.get("voice_eval") if isinstance(raw, dict) else None

    # 3) 재계산
    content_summary = {"average_score": content_avg}
    new_final = compute_final_score(
        content_summary, nonverbal_metrics_arg, voice_eval_arg,
        leniency_factor=leniency,
    )

    before = {
        "final_score_100":    float(s.final_score_100 or 0),
        "content_score_80":   float(s.content_score_80 or 0),
        "nonverbal_score_20": float(s.nonverbal_score_20 or 0),
    }
    after = {
        "final_score_100":    new_final["final_score_100"],
        "content_score_80":   new_final["content_score_80"],
        "nonverbal_score_20": new_final["nonverbal_score_20"],
    }

    if not body.dry_run:
        s.final_score_100    = after["final_score_100"]
        s.content_score_80   = after["content_score_80"]
        s.nonverbal_score_20 = after["nonverbal_score_20"]
        db.commit()

    return {
        "ok": True,
        "dry_run": body.dry_run,
        "leniency": leniency,
        "content_avg_from_evaluations": round(content_avg, 2),
        "before": before,
        "after": after,
        "delta": {k: round(after[k] - before[k], 2) for k in before},
    }
