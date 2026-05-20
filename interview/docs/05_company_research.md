# [5] 회사·직무 리서치

## 용도

사용자가 입력한 회사명·직무명·채용공고 텍스트(JD) 를 기반으로 면접 질문 생성에 사용할 회사·직무 정보를 구조화. 결과는 `company_job_summary` JSON 형식.

## 호출 위치

- 프롬프트 파일: `company_research_prompts.py`
- 호출 모듈: `company_research.py`
- 함수:
  - `summarize_company_job_from_text_async(...)` — 사용자가 JD 텍스트를 붙여넣었을 때
  - `research_company_from_name_async(...)` — 회사명·직무명만 있을 때 (자동 리서치, 학습 지식 기반)
  - `generate_company_questions_async(...)` — 회사 기반 일반 면접 질문 (현재 미사용 함수, 추후 확장용)
  - `format_company_block(...)` — JSON → 다른 프롬프트 주입용 텍스트 블록
  - `merge_candidate_and_company(...)` — 이력서 요약과 합쳐 단일 컨텍스트로

## 분기

| 사용자 입력 | 호출 |
|---|---|
| `company_text` 텍스트 있음 | `summarize_company_job_from_text_async` (JD 기반) |
| `company_name` 또는 `job_title` 만 | `research_company_from_name_async` (학습 지식 기반, "확인 불가" 표시) |
| 둘 다 비어있음 | 회사 컨텍스트 없이 진행 |

---

## COMPANY_RESEARCH_FROM_NAME_PROMPT (회사명만으로 자동 리서치)

```
당신은 한국어 채용 면접 준비를 위한 기업·직무 리서치 전문가입니다.

이 호출에서는 외부 실시간 웹 검색 도구가 활성화되어 있지 않습니다.
모델이 사전 학습한 일반 공개 지식만 사용하되,
확인이 어려운 부분은 반드시 "확인 불가" 라고 표시하세요.

목표:
지원자가 입력한 회사명과 직무명만으로 면접 질문 생성에 사용할 수 있는
company_job_summary JSON 을 작성하세요.

원칙:
1. 학습 시점 기준으로 알려진 공식 정보만 사용하세요.
2. 최근 이슈, 인사 변동, 특정 분기 실적, 최근 채용공고 내용 등 시점 의존 정보는 단정하지 마세요. "확인 불가" 또는 일반론으로 처리하세요.
3. 회사명이 모호하면 가장 가능성 높은 공개 회사를 가정하되, limitations 에 그 모호성을 반드시 명시하세요.
4. 직무 정보는 일반적으로 알려진 해당 직무의 핵심 역할·역량 위주로 작성하세요. 특정 회사의 내부 직제 추정은 하지 마세요.
5. 출처가 모호한 정보는 사실처럼 단정하지 말고, "확인 불가" 또는 일반론 표현을 사용하세요.
6. 민감 정보, 사생활 추정, 보호특성 관련 내용은 절대 포함하지 마세요.
7. 회사 정보와 직무 정보를 구분해서 정리하세요.
8. 압박질문 시드(pressure_points)는 직무 일반 리스크 중심으로 작성하고, 회사 고유 약점·논란 추정은 피하세요.

출력 규칙:
- 반드시 유효한 JSON만 출력.
- 마크다운/설명문/코드블록/주석 금지.
- 모든 리스트 필드는 비어도 빈 리스트([])로 출력.
- 확인 어려운 필드는 빈 문자열 "" 또는 "확인 불가" 로 처리.

출력 형식:
{
  "company_name": "회사명",
  "job_title": "직무명",
  "company_job_summary": {
    "company_overview": "회사 개요 (확인 불가일 경우 명시)",
    "business_relevance_to_job": "해당 직무와 회사 사업의 일반적 관련성",
    "main_business": ["주요 사업 영역"],
    "recent_strategy": ["일반적으로 알려진 전략 방향"],
    "job_role": ["직무의 일반적 역할"],
    "required_competencies": ["필요 역량"],
    "required_knowledge": ["필요 지식"],
    "required_technologies": ["관련 기술"],
    "work_context": ["업무 환경"],
    "core_values": ["일반적으로 알려진 핵심가치 또는 인재상"],
    "interview_keywords": ["면접 키워드"],
    "question_seed_points": [
      {"topic": "...", "why_it_matters": "...", "possible_question_type": "..."}
    ],
    "pressure_points": [
      {"point": "...", "reason": "...", "job_relevance": "..."}
    ]
  },
  "sources": [
    {"title": "모델 학습 지식 기반", "source_url": "확인 불가", "source_type": "model_training_knowledge", "used_for": [...]}
  ],
  "limitations": [
    "실시간 웹 검색 없이 학습 지식 기반으로 작성됨 — 최신 정보·세부 직제·내부 인재상은 부정확할 수 있음"
  ]
}
```

---

## COMPANY_JOB_SUMMARY_FROM_TEXT_PROMPT (붙여넣은 텍스트 요약)

```
당신은 한국어 채용 면접 준비를 위한 기업·직무 정보 요약 전문가입니다.

입력으로 제공되는 자료는 다음 중 하나 이상입니다.
- 회사 공식 홈페이지에서 가져온 텍스트
- 채용 홈페이지에서 가져온 텍스트
- 공식 채용공고
- 직무기술서
- 사업보고서 또는 공식 보도자료
- 사용자가 직접 붙여넣은 회사·직무 설명

목표:
입력 자료를 바탕으로 면접 질문 생성에 사용할 수 있는 company_job_summary JSON을 만드세요.

분석 기준:
1. 회사 정보와 직무 정보를 구분하세요.
2. 지원 직무와 직접 연결되는 내용만 우선적으로 추리세요.
3. 회사의 모든 사업을 나열하기보다 지원 직무와 관련 있는 사업을 중심으로 정리하세요.
4. 직무 역할, 필요 역량, 필요 지식, 관련 기술을 구체적으로 추출하세요.
5. 공식 자료에 없는 내용은 추정하지 마세요.
6. 사용자가 제공한 자료 안에서 확인 가능한 정보만 사용하세요.
7. 압박질문 생성을 위해 검증 가능한 직무 리스크 포인트를 도출하세요.
8. 지원자에게 불리한 방향으로 단정하지 말고, "검증 필요", "확인 필요" 수준으로 표현하세요.

출력 규칙:
- 반드시 유효한 JSON만 출력하세요.
- 마크다운, 설명문, 코드블록, 주석을 출력하지 마세요.
- 입력 자료에서 확인되지 않는 내용은 "확인 불가"라고 표시하세요.

출력 형식:
{
  "company_name": "회사명",
  "job_title": "직무명",
  "company_job_summary": { ... 위 FROM_NAME 과 동일 스키마 ... },
  "sources": [ ... ],
  "limitations": [ ... ]
}
```

---

## COMPANY_JOB_RESEARCH_SYSTEM_PROMPT (회사명 + 출처 텍스트 통합)

소스 파일 라인 17~92 참조. 실시간 웹 검색이 가능한 환경에서 공식 홈페이지/채용공고/직무기술서를 함께 입력하면 `research_summary` 출력. 현재는 호출되지 않음 — `summarize_company_job_from_text_async` 가 사실상 같은 역할을 수행. 외부 검색 API 연동 시 활성화 후보.

---

## COMPANY_JOB_BASED_QUESTION_PROMPT (회사·직무 기반 질문 N개)

`generate_company_questions_async` 가 사용. 현재 메인 파이프라인에서는 직접 호출하지 않고, 회사 컨텍스트는 `format_company_block` 으로 일반 질문 생성기에 주입하는 방식.

소스 파일 라인 176~227 참조. 출력 스키마:
```json
{
  "questions": [
    {
      "question": "면접 질문",
      "question_type": "company_fit | job_fit | technical_depth | experience_connection | motivation_commitment | problem_solving | pressure_verification | scenario_judgment",
      "company_basis": "...",
      "candidate_basis": "...",
      "evaluation_focus": "...",
      "expected_good_answer": "...",
      "possible_followup": "..."
    }
  ]
}
```

---

## COMPANY_JOB_RESEARCH_VALIDATION_PROMPT (검증)

생성된 `company_job_summary` 가 면접 질문 생성에 사용해도 되는 수준인지 검토. 현재 호출 안 함 — 추후 품질 게이트로 추가 가능.

소스 파일 라인 232~262 참조.

---

## 출력 → 다른 프롬프트로의 주입

`format_company_block(summary)` 가 JSON 을 다음과 같은 텍스트 블록으로 변환:

```
회사: <name> / 직무: <title>
- 회사 개요: ...
- 직무 연관성: ...
- 주요 사업: ...
- 최근 전략: ...
- 직무 역할: ...
- 필요 역량: ...
- 필요 지식: ...
- 관련 기술: ...
- 업무 환경: ...
- 핵심가치/인재상: ...
- 면접 키워드: ...
- 면접 시드: ...
- 검증 포인트: ...
```

이 블록을 `merge_candidate_and_company()` 가 이력서 요약 뒤에 `[지원 회사·직무 컨텍스트]` 헤더와 함께 부착 → 모든 후속 질문 생성기(`generate_question_texts_async`, `enrich_question_async`, `generate_followup_async`, `generate_pressure_question_async`, `generate_company_job_pressure_question_async`) 가 자동으로 같은 컨텍스트를 보게 됨.
