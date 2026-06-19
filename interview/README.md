# Signal Catch ⚡

> AI 모의면접 시뮬레이터 — **언어·시각·음성 신호** 를 한 번에 분석해 100점 만점 종합 채점.
> 캡스톤 디자인 프로젝트.

브라우저 하나면 끝. 카메라로 얼굴 안정성, 마이크로 말 속도·침묵을 측정하고,
OpenAI GPT 가 답변 내용을 80점 만점으로 평가합니다.
면접관처럼 꼬리 질문·압박 질문을 즉석에서 만들어 던지고,
이력서·자기소개서·이전 답변과의 **일관성** 까지 검증합니다.

---

## 핵심 기능

### 면접 진행
- **이력서 기반 맞춤 질문** + 회사·직무 리서치 (LLM 자동 또는 JD 텍스트)
- **꼬리 질문** — 답변 깊이가 부족하면 자동 추적 (decision gate + strictness)
- **압박 질문** — 1~10 단계 강도 조절 가능 (사실 확인·근거 추궁)
- **3가지 모드**
  - 일반: 텍스트/음성 답변 자유 선택, 중간 재개 가능
  - 녹화만: 화면엔 영상 안 보이되 비언어 분석 백그라운드 진행
  - **실전 모드**: 질문 10초 노출 후 자동 녹음 시작, 재녹음 금지, 중도 이탈 시 자동 폐기

### 채점 (100점 만점, 비례 환산)
- **답변 내용 80점** — GPT 가 공통 6항목(50점) + 질문별 맞춤 평가(30점)
- **시각 비언어 10점** — MediaPipe 얼굴 인식 (표정·시선·자세)
- **음성 비언어 10점** — Whisper word-level timestamp (말 속도·침묵·반복어·전달)
- **자동 환산** — 카메라 OFF + 텍스트 답변이면 만점이 80점 → ×1.25 환산
- 한 축만 있으면 그 축이 만점 20점을 흡수

### 결과 분석
- 질문별 점수 막대 + 강점/개선/모범 답변
- **시각 측정 지표 시간 추이** — SVG 라인 차트로 누적 평균 변화 시각화
- **답변 일관성 검증** — 이력서·자기소개서·이전 답변 ↔ 현재 답변 비교 (점수 미반영)
- **회사·직무 리서치 카드** — LLM 이 회사 정보를 어떻게 정리했는지 노출

### 관리·운영
- 사용자별 **Credit 재화** (면접 1회 = 1 credit, admin/moderator 는 무제한)
- 이메일 인증 (Gmail SMTP 또는 콘솔 fallback)
- Google OAuth 로그인 + 자체 로컬 로그인
- **관리자 디버그 옵션** (질문 편향·채점 보정 슬라이더)
- **점수 재계산 도구** — leniency 보정 적용된 옛 세션을 원본으로 복구

---

## 빠른 시작

### 1) 가상환경 + 의존성

```bash
git clone https://github.com/본인계정/signal-catch.git
cd signal-catch

# macOS — iCloud Drive 영향 받는 ~/Documents 안이라면 .nosync 폴더 권장
python3 -m venv venv.nosync && ln -s venv.nosync venv
# 일반 환경:
# python3 -m venv venv

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2) 환경변수

```bash
cp .env.example .env
# .env 편집 (아래 표 참고)
```

| 변수 | 필수 | 설명 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | https://platform.openai.com/api-keys (sk-... 형태) |
| `SESSION_SECRET_KEY` | ✅ | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | — | 비우면 로컬 `mocktalk.db` (SQLite). Supabase 면 Transaction Pooler URL |
| `GOOGLE_CLIENT_ID/SECRET` | — | Google 로그인 쓸 때만 |
| `SMTP_USER` / `SMTP_APP_PASSWORD` | — | Gmail 앱 비밀번호 — 이메일 인증 발송. 미설정 시 콘솔에 링크 출력 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | — | 영상 클립을 Supabase Storage 에 업로드할 때 |

### 3) 실행

```bash
uvicorn main:app --reload
# → http://localhost:8000
```

처음 가입한 사용자는 **일반 user** 로 시작합니다. 본인을 admin 으로 만들려면 한 번만 SQL:

```bash
# SQLite
sqlite3 mocktalk.db "UPDATE users SET role='admin', credits=999 WHERE email='본인gmail@gmail.com';"

# Supabase 면 SQL Editor 에서 동일한 UPDATE 실행
```

이후 헤더 우측 빨간 **관리** 링크 → `/admin` 에서 다른 사용자 권한·credit 자유롭게 조정.

---

## 점수 체계

```
┌─ 답변 내용 80점 ────────────────────┐
│  공통 평가  50점  (6항목)          │
│  맞춤 평가  30점  (질문별 포인트)   │
├─ 비언어 20점 ─────────────────────┤
│  시각 비언어 10점                  │
│   ├─ 표정 안정성 3 (음성 분담 시)   │
│   ├─ 시선 안정성 5                 │
│   └─ 자세 안정성 2                 │
│  음성 비언어 10점                  │
│   ├─ 말 속도 3                     │
│   ├─ 침묵·끊김 3                   │
│   ├─ 반복어·더듬음 2                │
│   └─ 전달 안정성 2                 │
└────────────────────────────────────┘
```

빠진 항목이 있으면 그만큼 만점에서 제외되고 **100점으로 비례 환산** (예: 카메라 OFF + 텍스트 답변 → 만점 80 → ×1.25). UI 에는 항상 `/100` 으로 표시됩니다.

---

## 디렉터리 구조

```
.
├── main.py                          # FastAPI 앱 진입 (라우터·SSE·credit·email 인증 게이트)
│
├── app/                             # 도메인별 비즈니스 로직 (모두 패키지)
│   ├── scoring/                     # 채점·credit·token usage
│   │   ├── final_score.py           # 100점 환산 (비례 환산 + leniency 보정)
│   │   ├── answer_evaluator.py      # 답변 채점 (80점, JSON 출력)
│   │   ├── credit_ops.py            # Credit 입출·잔액
│   │   └── openai_usage.py          # 토큰 사용량 적재 (contextvars)
│   │
│   ├── questions/                   # 질문 생성·검증
│   │   ├── question_generator.py    # 이력서 → 질문 (개인 + 공통 + role-specific)
│   │   ├── followup_generator.py    # 꼬리 질문 (decision gate)
│   │   ├── pressure_generator.py    # 압박 질문 (1~10단계 + 회사 기반)
│   │   └── consistency_checker.py   # 자료 ↔ 답변 일관성 검증
│   │
│   ├── nonverbal/                   # 비언어 분석·평가
│   │   ├── nonverbal_analyzer.py    # 서버측 CV (cv2 + mediapipe, CLI 백업)
│   │   ├── nonverbal_feedback.py    # 시각 비언어 LLM 코칭
│   │   └── voice_nonverbal_eval.py  # 음성 비언어 평가 (말 속도·침묵·반복어)
│   │
│   ├── company/                     # 회사·직무 조사
│   │   ├── company_research.py      # 회사·직무 정보 정리 (auto / from JD text)
│   │   └── company_research_prompts.py
│   │
│   ├── analysis/                    # 심화 리포트
│   │   ├── deep_analysis.py         # 심화 분석 (gpt-5.5 권장)
│   │   └── report_writer.py
│   │
│   └── services/                    # 외부 I/O (STT·메일·스토리지)
│       ├── speech_to_text.py        # Whisper STT + 환각 필터
│       ├── email_service.py         # Gmail SMTP
│       ├── storage.py               # Supabase Storage 업로드
│       └── resume_loader.py         # 이력서 PDF/문서 파싱
│
├── auth/                            # Google OAuth + 로컬 로그인 + 의존성
├── db/                              # SQLAlchemy 모델·세션·마이그레이션 SQL
│   └── migrations/                  # Postgres SQL (SQLite 는 init_db 가 자동 처리)
├── routers/                         # google_auth_routes, local_auth_routes, admin_routes, sessions_routes, board_routes
│
├── static/                          # 모든 HTML/JS/CSS (Tailwind CDN)
│   ├── index.html                   # 메인 면접 진행 화면
│   ├── result.html                  # 면접 직후 결과
│   ├── session_detail.html          # 과거 면접 상세
│   ├── analysis.html                # 심화 분석
│   ├── board.html                   # 공유 게시판
│   ├── admin.html / admin_user.html
│   ├── face_analyzer.js             # MediaPipe Tasks Vision (브라우저)
│   ├── nonverbal_charts.js          # SVG 시간 추이 차트
│   ├── nonverbal_extras.js          # 음성·일관성·회사 카드 공용 렌더러
│   └── admin_chrome.js              # 헤더 chip (credit·이메일 인증 배너)
│
├── docs/                            # AI 프롬프트 모음 (조원 공유용)
├── scripts/                         # 운영용 보조 스크립트 (run_interview, test_db_connection)
└── tools/                           # 일회성 스크립트 (recompute_final_score 등)
```

---

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| 백엔드 | Python 3.12 · FastAPI · Uvicorn · SQLAlchemy 2 · psycopg3 |
| 프론트엔드 | HTML + Tailwind CSS (CDN) + Vanilla JS (ES Modules) |
| DB | Supabase Postgres (운영) / SQLite (로컬) |
| AI | OpenAI GPT-4o-mini · GPT-5 시리즈 · Whisper-1 |
| 컴퓨터 비전 | MediaPipe Tasks Vision 0.10.3 (브라우저 WASM) |
| 인증 | authlib (Google OAuth) · bcrypt (로컬) · 이메일 인증 |
| 영상 저장 | Supabase Storage (옵션) |

---

## 관리자 디버그 옵션

`/admin` 권한이 있으면 메인 페이지 시작 폼에 🔧 **디버그 옵션 (관리자 전용)** 박스가 펼쳐집니다:

| 옵션 | 효과 |
|---|---|
| 주제 편향 | 기술·인성·경험·압박 중 한 방향으로 비중 기울이기 |
| 난이도 편향 | 기초·심화 |
| 질문 스타일 | 구조적(STAR)·대화형 |
| 키워드 강제 주입 | `Redis, A/B 테스트` 같이 지정 시 LLM 이 질문에 포함 |
| 기업·직무 맞춤 추가 | role-specific 질문 3개 추가 생성 |
| 채점 보정 (0.1× ~ 3.0×) | 모든 점수에 동일 계수 곱 — clamp 적용 |

`role=admin` 이 아닌 사용자가 폼 위변조로 보내도 서버에서 무시됩니다.

**점수 재계산 도구** — 세션 상세 페이지(`/session/{code}`)에서 빨간 "점수 재계산" 버튼 → leniency=1.0 으로 원본 점수 복원.

---

## 마이그레이션

- **SQLite (로컬)**: `init_db()` 가 컬럼·테이블 자동 추가 — 서버 재시작이면 끝.
- **Postgres (Supabase 등)**: `db/migrations/` 의 SQL 파일을 SQL Editor 에서 한 번씩 실행.

```
db/migrations/
├── migration_001_add_user_auth.sql
├── migration_002_share_softdelete.sql
├── ...
├── 2026-05-21_credits_and_token_usage.sql
├── 2026-05-21_email_verification.sql
└── ...
```

---

## 보안 주의

- `.env` 는 **절대** commit 금지 — `.gitignore` 에 강제 차단됨
- 운영 배포 시 `SESSION_SECRET_KEY` 는 반드시 새 랜덤 값으로 교체
- Supabase `service_role` 키는 서버에만, 클라이언트 노출 금지
- 이메일·녹음·영상은 사용자 개인정보 — `sessions/`, `uploads/` 가 `.gitignore` 에 포함됨

---

## 라이선스

캡스톤 디자인 학생 프로젝트로 제작. 학습·연구 용도 외 상업적 사용 시 별도 문의 부탁드립니다.

---

## 만든 사람

세종대학교 전자공학과 · 캡스톤 디자인 팀
