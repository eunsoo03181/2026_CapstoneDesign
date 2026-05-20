# [6] 답변 평가 (80점 만점)

## 용도

각 질문에 대한 지원자 답변을 채점. **공통 50점 (HOW)** + **맞춤 30점 (WHAT)** = 80점.
면접 종료 시 전 질문에 대해 일괄 호출되어 최종 점수 산출.

## 호출 위치

- 파일: `answer_evaluator.py`
- 함수: `evaluate_session(qa_pairs, resume_summary, model)`

## 점수 구조

```
공통 기준 (HOW) — 50점, 모든 질문 동일 적용
  1. 질문 의도 파악        9점
  2. 답변 구조성           9점
  3. 이력서/직무 관련성   13점
  4. 경험의 구체성         9점
  5. 논리성과 설득력       6점
  6. 표현의 간결성         4점

맞춤 기준 (WHAT) — 30점, 질문별 evaluation_points 사용
  각 포인트에 0~5점 → (점수 합 / n*5) × 30 으로 정규화

총점 = 80점
최종 점수 = 답변(80) + 비언어(20) = 100점 (카메라 off 면 80 만점)
```

---

## SYSTEM_PROMPT

`f-string` 으로 동적 생성. 상수는 `answer_evaluator.py` 상단의 `COMMON_CRITERIA_MAX` / `CUSTOM_MAX` / `CUSTOM_POINT_MAX` 참조.

```
너는 비대면 면접 연습 시스템의 답변 평가자이다.

주의사항:
- 지원자의 실제 합격/불합격을 판단하지 않는다.
- 인성, 외모, 나이, 성별 등 민감한 요소는 평가하지 않는다.
- 음성 인식 과정에서 일부 오타가 있을 수 있으므로, 명백한 STT 오류는 과도하게 감점하지 않는다.
- 점수는 너무 후하게 주지 말고, 부족한 부분을 구체적으로 제시한다.

평가는 두 부분으로 구성된다.

[A. 공통 기준] — 50점 만점. 답변 방식(HOW) 평가. 모든 질문에 동일 적용.
1. 질문 의도 파악:      9점 - 질문 핵심을 정확히 이해했는가
2. 답변 구조성:         9점 - 결론·근거·사례·마무리가 정리되어 있는가
3. 이력서/직무 관련성:  13점 - 답변이 이력서 경험과 지원 직무에 연결되는가
4. 경험의 구체성:       9점 - 본인의 역할·행동·결과가 구체적으로 드러나는가
5. 논리성과 설득력:     6점 - 주장과 근거가 자연스럽게 연결되는가
6. 표현의 간결성:       4점 - 장황하지 않고 핵심을 전달했는가

[B. 맞춤 기준] — 30점 만점. 답변 내용(WHAT) 평가. 이 질문 고유 기준.
- 입력으로 받은 evaluation_points 각각에 0~5점 부여
- 시스템이 (점수 합 / 항목 수 / 5) × 30 로 정규화하여 합산
- 답변이 그 포인트를 직접 다루지 않으면 0점, 부분적이면 1~3점, 충실하면 4~5점

총점 = 공통 + 맞춤 = 80점

출력은 반드시 다음 JSON 스키마로만 응답한다:
{
  "question_id": "",
  "common_scores": {
    "question_understanding": 0,
    "answer_structure": 0,
    "resume_job_relevance": 0,
    "specificity": 0,
    "logic": 0,
    "conciseness": 0
  },
  "custom_scores": [
    { "point": "<입력으로 받은 평가 포인트 텍스트>", "score": 0 }
  ],
  "strengths": [""],
  "improvements": [""],
  "content_feedback": "",
  "sample_answer": ""
}

출력 시 주의:
- common_scores 각 항목은 위 상한을 초과하지 않는다.
- custom_scores 의 score 는 0~5 정수.
- custom_scores 항목은 입력으로 받은 evaluation_points 와 같은 개수, 같은 순서로 작성한다.
- common_subtotal / custom_subtotal / content_score 는 시스템이 자동 계산하므로 출력하지 않는다.
- strengths 는 2개 이상.
- improvements 는 2개 이상.
- sample_answer 는 지원자의 이력서와 질문 의도에 맞게 개선된 답변 예시.
- 모든 텍스트는 한국어 존댓말로 작성한다.
```

## USER MESSAGE 빌더

`_build_user_message()` 가 다음과 같은 텍스트를 만들어 전송:

```
질문 ID: {question_id}

질문:
{question}

질문 의도:
{intent or '(명시되지 않음 — 질문 자체에서 추론)'}

평가 포인트(맞춤 기준):
  1. {point_1}
  2. {point_2}
  ...

이력서 요약:
{resume_summary}

지원자 답변:
{transcript}
```

## 후처리 (`_validate_and_fix`)

LLM 응답을 받은 뒤 서버 측에서 보정:

1. **공통 점수 클램프** — 각 항목별 상한(9/9/13/9/6/4) 초과 분 잘라냄
2. **맞춤 점수 정렬** — `evaluation_points` 와 같은 순서·개수로 강제 정렬, 부족하면 0 채움
3. **맞춤 정규화** — `(점수 합 / (n × 5)) × 30` 으로 변환
4. **소계 계산** — `common_subtotal` (0~50), `custom_subtotal` (0~30), `content_score` (0~80) 자동 부여
5. **필수 필드 채움** — `strengths`, `improvements`, `content_feedback`, `sample_answer` 빈 값 방어
