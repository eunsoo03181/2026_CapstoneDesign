# [8] 심층 분석 (Deep Analysis)

## 용도

면접 결과 페이지(`/analysis/{code}`) 의 "심층 분석" 버튼이 눌리면, 면접 전체에 대해 가장 비싼 모델(`gpt-5.5`) 로 markdown 보고서를 생성. 한 줄 요약 / 답변 패턴 / 강점·약점 톱5 / 공통 항목 심층 코멘트 / 7일 액션 플랜 / 가상 인사담당자 종합 코멘트.

## 호출 위치

- 파일: `deep_analysis.py`
- 함수: `generate_deep_analysis(session_payload, model=DEEP_MODEL_DEFAULT)`
- 모델: `gpt-5.5` 고정 (`DEEP_MODEL_DEFAULT`)
- 엔드포인트:
  - `POST /api/sessions/{code}/deep-analysis` — 생성 (또는 cached 반환, `?force=true` 로 재생성)
  - `GET /api/sessions/{code}/deep-analysis` — 저장된 결과 조회

## 저장

`InterviewSession.deep_analysis_md` 컬럼에 markdown 그대로 캐시. 같은 세션은 한 번만 생성하면 재호출 시 즉시 반환. `?force=true` 면 재생성.

## 입력

`_build_full_detail()` 가 반환하는 `session_payload` 형태 — 질문/답변/평가 결과/비언어 점수까지 포함.

함수 내부 `_format_questions_block()` 가 다음과 같이 직렬화:

```
### Q1 — 질문 텍스트
질문 의도: ...
평가 포인트: [...]

답변:
...

채점: 65 / 80
공통: 의도파악 8/9, 구조 7/9, 관련성 11/13, ...
  - 맞춤기준 [점검 포인트 1] 4/5
강점: ...
개선점: ...
총평: ...

---

### Q2 — ...
```

자료 헤더에는:
```
제목: <title>  (코드 #<code>)
최종 점수: <final_score_100> / 100
답변 점수: <content_score_80> / 80
비언어 점수: <nonverbal_score_20> / 20

비언어 요약:
- summary: ...
- smile: ...
- focus: ...
- blink: ...
- posture: ...
```

---

## SYSTEM_PROMPT

```
당신은 30년 경력의 대기업 인사담당 임원입니다.
지원자의 모의 면접 답변과 채점 결과를 받아, 더 깊고 친절한 한국어 보고서를
markdown 형식으로 작성합니다.

원칙:
- 추측이 아닌 답변 내 근거로 말한다. 인용은 "..."로 짧게.
- 칭찬 위주로 흐르지 말고, 약점은 약점이라고 명확히 짚는다.
- 그러나 어조는 코칭하는 멘토처럼 따뜻하고 단정적이지 않게.
- 사실에 없는 경력/스킬을 만들어내지 않는다.
- 이모지를 사용하지 않는다.
- 글머리 기호와 짧은 단락을 자주 활용해 가독성을 높인다.

출력 구조 (반드시 이 순서로):
# 심층 분석 보고서

## 한 줄 요약
## 답변 패턴 — 무엇이 인상적이었고 무엇이 아쉬웠나
## 강점 톱5
## 약점 톱5 — 우선 개선 순
## 공통 항목 심층 코멘트
   - 질문 의도 파악
   - 답변 구조성
   - 이력서/직무 관련성
   - 경험 구체성
   - 논리·설득력
   - 표현의 간결성
## 7일 액션 플랜
## 가상 인사담당자 종합 코멘트
```

## 출력

순수 markdown 문자열 (JSON 아님). 클라이언트(`analysis.html`)가 `marked.js` 로 렌더.

API 응답:
```json
{
  "available":    true,
  "public_code":  "<code>",
  "markdown":     "# 심층 분석 보고서\n\n## 한 줄 요약\n...",
  "model":        "gpt-5.5",
  "generated_at": "2026-05-13T12:34:56",
  "cached":       false
}
```
