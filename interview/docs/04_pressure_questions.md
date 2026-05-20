# [4] 압박 질문 (Pressure)

## 용도

지원자의 감정을 흔드는 질문을 통해 평정심·사고력·대응력을 테스트하는 면접 기법.
**감정 공격이 아니라**, 이력서/답변에서 검증이 필요한 부분을 근거로 한 도전적 질문.

## 호출 위치

- 파일: `pressure_generator.py`
- 함수:
  - `decide_pressure_question_async(...)` — 게이트 (필요한지 판정)
  - `generate_pressure_question_async(...)` — 이력서 기반 압박질문 N개 생성
  - `generate_pressure_followup_question_async(...)` — 직전 답변의 용어·누락·근거 부족 검증
  - `generate_company_job_pressure_question_async(...)` — 회사·직무 적합성 간극 기반
  - `can_generate_pressure_question(...)` — 호출측 하드 한도 헬퍼

## 모드 + 한도

| 모드 | 동작 | 카테고리별 캡 (사용자 본 질문 ≥5) | <5 인 경우 |
|---|---|---|---|
| `off` | 비활성 | 0 / 0 / 0 | 0 / 0 / 0 |
| `additive` | 일반 질문에 일부 압박 섞음 | 공통 1 · 맞춤 1 · 꼬리 2 | 1 / 1 / 1 |
| `focused` (코드 보존, UI 미노출) | 압박 위주 | ∞ · ∞ · ∞ (체인 깊이만 유효) | — |

## 압박 유형 8가지 (꼬리질문 전용)

- `term_depth_probe` — 답변에 나온 용어 정확도 검증
- `concept_definition_probe` — 핵심 개념을 자기 말로 정의
- `mechanism_probe` — 원리·절차·작동 방식 설명
- `omission_probe` — 원질문이 요구한 핵심 누락 검증
- `evidence_probe` — 주장·성과의 근거·수치·검증 방법
- `application_probe` — 실제 직무 적용
- `boundary_probe` — 한계·예외·실패 가능성 인식
- `ownership_probe` — 팀 vs 본인 기여 구분

## 일반 압박 유형 (이력서/회사 기반)

`claim_verification | role_ownership | technical_depth | job_fit_gap | decision_challenge | failure_learning | conflict_ethics | motivation_commitment | priority_tradeoff | feedback_resilience`

---

## PRESSURE_CRITICISM_LEVEL_POLICY (공통 — 수위 0~10 정책)

전체 압박 생성기 system 메시지 끝에 자동으로 부착됩니다.

```
압박 수위는 0부터 10까지의 정수로 조절합니다.
0은 표준적인 구조화 면접 수준의 검증 질문이고,
10은 이력서와 자기소개서를 매우 비판적으로 검토하여 가장 날카로운 직무 관련 검증 질문을 만드는 수준입니다.

중요:
압박 수위가 높아질수록 질문의 비판성, 회의적 관점, 검증 강도는 높아지지만,
무례함, 인격 공격, 조롱, 위협, 차별, 사생활 침해는 절대 허용되지 않습니다.
압박 수위는 '말투의 공격성'이 아니라 '직무 관련 주장에 대한 검증 강도'를 의미합니다.

수위별 기준:

0단계 - 표준 검증형
- 중립적으로 경험·성과 확인. 표현은 부드럽고 균형적.

1단계 - 약한 검증형
- 모호한 부분을 부드럽게 확인. 설명 기회 넓게 제공.

2단계 - 근거 요청형
- 구체적 근거 요구. 확인 중심.

3단계 - 모호성 지적형
- 불명확한 표현 직접 지적. 표현은 완곡.

4단계 - 간극 확인형
- 직무 요구와 경험 차이를 명확히.

5단계 - 균형적 비판형
- 합리적 의문 제기. 외부 요인/팀 vs 개인 기여 검증.

6단계 - 명시적 우려 제기형
- 면접관 우려를 직접 제시. "보일 수 있다" 표현.

7단계 - 강한 검증형
- 핵심 주장에 대한 엄격한 검증.

8단계 - 반론 제시형
- 불리한 해석/반론 제시. 논리적 방어 기회.

9단계 - 매우 비판적 검증형
- 가장 약한 지점/과장/부적합 리스크 집중. 표현은 날카롭지만 전문적.

10단계 - 최대 비판적 검증형
- 가장 치명적인 직무 관련 리스크 검증. 모욕/차별/사생활은 절대 금지.

레벨 적용 규칙:
1. level↑ → 약점·모호함·과장·부적합 리스크를 더 적극 탐지.
2. level↑ → 더 직접적이고 날카롭게 작성.
3. 모든 level → 정중한 한국어 존댓말 한 문장.
4. 모든 level → 이력서에 없는 사실 단정 금지.
5. 모든 level → 인격이 아닌 경험·주장·성과·판단·직무 적합성만 검증.
6. 0~2: 부드러운 확인형 표현.
7. 3~5: 모호성·간극·근거 부족 명확히 지적.
8. 6~8: 면접관 우려·반론 제시.
9. 9~10: 불리한 평가 가능성 + 반박 근거 요구.
10. 애매하면 사용자가 지정한 level 보다 한 단계 낮은 강도로.
```

---

## PRESSURE_QUESTION_DECISION_POLICY (공통 — 생성 여부 판정 정책)

```
압박질문 생성 여부 판단 기준:

압박질문은 다음 조건을 모두 만족할 때만 생성합니다.

1. 명시적 근거가 있어야 합니다 (이력서/자소서/원래 질문/지원자 답변 중 하나).
2. 직무 관련성이 높아야 합니다 (실제 수행 역량 연결).
3. 중요한 평가 리스크가 있어야 합니다 (성과 신뢰도/기여도/기술 깊이/직무 적합성 등).
4. 일반 질문보다 압박형 질문이 더 적합해야 합니다.
5. 이미 답변된 내용이 아니어야 합니다.
6. 면접 흐름을 해치지 않아야 합니다.
7. 공정성 리스크가 없어야 합니다 (민감 정보 금지).
8. 이력서/답변에 없는 사실 단정 금지 — '~로 평가될 수 있는데' 처럼 표현.

압박질문 생성 X 경우:
- 현재 자료로 평가 기록이 가능
- 압박 포인트 약함/직무 관련성 낮음
- 비판 근거 불명확
- 일반 꼬리질문으로 충분
- 이미 같은 취지 질문
- 압박 최대 횟수 도달
- 불필요한 방어 태도만 유발
- 민감 정보/보호특성으로 이어질 위험

중요:
criticism_level이 높더라도 압박질문을 억지로 만들지 마세요.
높은 level = '근거가 있을 때 더 비판적으로 검증', '근거 없어도 비판 포인트 만들어낸다' 아님.
```

---

## 프롬프트 일람 (소스 라인 매핑)

전체 길이가 매우 길어 소스 파일에서 직접 참조하는 게 가장 안전합니다.
파일: `pressure_generator.py`

| 상수명 | 라인 | 용도 |
|---|---|---|
| `PRESSURE_QUESTION_SYSTEM_PROMPT_MINI` | 89~139 | 기본 생성 (수위 미적용, gpt-4o-mini) |
| `PRESSURE_QUESTION_SYSTEM_PROMPT_ADVANCED` | 142~233 | 기본 생성 (수위 미적용, 고급) |
| **`PRESSURE_CRITICISM_LEVEL_POLICY`** | 240~296 | 수위 정책 (위에 인라인) |
| `PRESSURE_QUESTION_SYSTEM_PROMPT_MINI_WITH_LEVEL` | 303~354 | **현재 사용 중** — 수위 적용 생성, MINI |
| `PRESSURE_QUESTION_SYSTEM_PROMPT_ADVANCED_WITH_LEVEL` | 356~459 | **현재 사용 중** — 수위 적용 생성, ADVANCED |
| **`PRESSURE_QUESTION_DECISION_POLICY`** | 461~489 | 게이트 정책 (위에 인라인) |
| `PRESSURE_GATE_SYSTEM_PROMPT_MINI` | 491~541 | **게이트** — MINI 판정 |
| `PRESSURE_GATE_SYSTEM_PROMPT_ADVANCED` | 543~636 | **게이트** — ADVANCED 판정 |
| `PRESSURE_FOLLOWUP_SYSTEM_PROMPT_ADVANCED` | 802~896 | **답변 기반 압박 꼬리질문** — 용어/누락/근거 검증 |
| `COMPANY_JOB_PRESSURE_QUESTION_PROMPT` | 957~1014 | **회사·직무 적합성 간극** 기반 압박 |

## 답변 기반 압박 꼬리질문 출력 형식

```json
{
  "should_ask_followup": true,
  "followup_type": "term_depth_probe | concept_definition_probe | mechanism_probe | omission_probe | evidence_probe | application_probe | boundary_probe | ownership_probe",
  "detected_terms": ["직전 답변에서 추출한 핵심 용어"],
  "detected_gap": "답변에서 설명이 부족하거나 검증이 필요한 부분",
  "basis_from_answer": "직전 답변 중 질문의 근거가 된 표현 요약",
  "question": "정중한 한국어 압박 꼬리질문 한 문장",
  "evaluation_focus": "이 질문으로 확인하려는 평가 초점",
  "good_response_signals": ["..."],
  "weak_response_signals": ["..."],
  "risk_check": {
    "based_on_candidate_answer": true,
    "job_related": true,
    "not_personal_attack": true,
    "avoids_sensitive_information": true,
    "does_not_assume_unstated_facts": true
  }
}
```

## 회사·직무 기반 압박 출력 형식

```json
{
  "pressure_questions": [
    {
      "question": "정중한 한국어 한 문장",
      "pressure_type": "job_fit_gap | technical_depth | role_ownership | motivation_commitment | scenario_judgment | responsibility_check | risk_response",
      "company_basis": "회사·직무 요구사항 근거",
      "candidate_basis": "지원자 경험 근거",
      "detected_gap": "검증이 필요한 간극",
      "evaluation_focus": "...",
      "good_response_signals": ["..."],
      "weak_response_signals": ["..."],
      "risk_check": {
        "based_on_official_company_info": true,
        "based_on_candidate_material": true,
        "job_related": true,
        "not_personal_attack": true,
        "avoids_sensitive_information": true,
        "does_not_assume_unstated_facts": true
      }
    }
  ]
}
```

## 안전 가드 (`_risk_check_passed`)

LLM 출력의 `risk_check` 안에 다음 4개 플래그 중 하나라도 `false` 면 생성 거절:

- `job_related`
- `not_personal_attack`
- `avoids_sensitive_information`
- `does_not_assume_unstated_facts`

## 좋은 / 나쁜 표현 예시

✅ 좋은 표현:
- "이력서상 성과가 팀 단위로 표현되어 있는데, 그중 본인이 직접 의사결정하고 실행한 부분은 어디까지였습니까?"
- "해당 기술을 사용했다고 하셨지만 운영 중 장애나 성능 문제를 다룬 경험은 명확하지 않은데, 실제로 가장 깊게 관여한 기술적 문제는 무엇이었습니까?"
- "현재 이력서만 기준으로 보면 지원 직무의 핵심 요구사항을 바로 수행하기 어렵다는 평가가 나올 수 있는데, 이를 반박할 수 있는 가장 강한 근거는 무엇입니까?"
- "성과가 인상적이지만 외부 요인도 있었을 수 있는데, 본인의 기여와 외부 요인을 어떻게 구분해 설명하시겠습니까?"
- "그 판단에 대해 팀원이 강하게 반대한다면, 어떤 근거를 다시 확인하고 어떻게 설득하시겠습니까?"

❌ 금지 표현:
- "지원자의 역량은 우리 회사에 필요 없어 보이는데요?"
- "정말 본인이 한 게 맞나요?"
- "그건 완전히 틀린 생각 아닌가요?"
- "그 정도 경험으로 어떻게 일하려고 하나요?"
- "나이가 어린데 버틸 수 있겠어요?"
- "결혼 후에도 야근할 수 있나요?"
- "건강상 문제는 없나요?"
- "어느 지역 출신이라서 그런가요?"
- "상사가 시키면 무조건 해야 하는 것 아닌가요?"
