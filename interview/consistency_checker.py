"""
답변 일관성 검증 — 이력서 / 자기소개서 / 이전 답변 ↔ 현재 답변 비교.

답변 채점(80점) 과는 분리. 점수에는 영향 X, "면접관이 의심할 만한 포인트" 를
플래그로 띄워 사용자에게 보여줍니다.

원안: 사용자 제공 ai_language_eval.py 의 consistency_check 블록.
변경:
  - 평가/꼬리질문 부분 제거, 일관성 검증만 단일 책임
  - 비공식적 단정 표현 금지 ('거짓말' 등) 규칙 유지
  - 모델 항상 gpt-4o-mini (저비용, JSON 응답 안정)
"""

import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI


CONSISTENCY_SYSTEM_PROMPT = """너는 비대면 면접 연습 시스템의 답변 일관성 검증 도우미다.

[역할]
- 지원자의 현재 답변을 이력서·자기소개서·이전 답변들과 비교해 일관성을 평가한다.
- "면접관이라면 추가로 확인하고 싶을 부분" 을 객관적으로 짚어준다.
- 점수를 매기지 않는다. 의심·진위·인성·외모 평가 금지.

[중요 제한]
- "거짓말", "허위", "기만" 같은 단정 표현 절대 금지.
- 대신 "추가 확인 필요", "구체화 필요", "일관성 확인 필요" 로 표현한다.
- 사실 단정 대신 "~로 보일 수 있다", "~가 명확치 않다" 같은 완곡 표현 사용.
- STT 사소한 오타·맞춤법은 무시한다.

[확인 항목]
1. 이력서/자기소개서와 현재 답변의 역할·기간·기술·성과가 다르게 표현되었는가?
2. 이전 답변과 현재 답변 사이에 상반·모순되는 내용이 있는가?
3. 큰 성과를 주장했지만 수치·행동·결과 근거가 부족한가?
4. 기술 용어를 언급했지만 실제 이해도·적용 과정 설명이 부족한가?
5. 본인의 직접 경험인지 불명확한 표현이 있는가?

[일관성 레벨]
- "없음": 이상 신호 없음 — 자료와 답변이 일관됨
- "낮음": 사소한 구체화 필요 1건 이하
- "보통": 명확한 구체화/확인 필요 2~3건
- "높음": 자료와의 차이가 크거나 자체 모순이 다수

[출력 형식 — 반드시 JSON]
{
  "level": "없음/낮음/보통/높음",
  "summary": "1~2문장 요약 (단정 표현 금지)",
  "issues": [
    {
      "type": "이력서-답변 차이/자기소개서-답변 차이/이전 답변-현재 답변 차이/근거 부족/기술 이해도 확인/역할 구체화 필요/성과 구체화 필요",
      "evidence": "근거가 된 발언 인용 (짧게)",
      "reason": "왜 확인이 필요한지 (단정 X)",
      "recommended_question": "면접관이 자연스럽게 물어볼 만한 후속 질문 (선택)"
    }
  ]
}
"""


def check_consistency(
    question: str,
    current_answer: str,
    *,
    resume_summary: str = "",
    cover_letter_summary: str = "",
    previous_answers: Optional[List[Dict[str, str]]] = None,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    답변 1개에 대해 일관성 검증.
    previous_answers: [{"question": "...", "answer": "..."}, ...] (현재 답변 직전까지)
    반환: {level, summary, issues[]}.
    호출 실패 시 level="없음", issues=[] 폴백.
    """
    if not current_answer or not current_answer.strip():
        return {"level": "없음", "summary": "답변 없음", "issues": []}

    payload = {
        "question": question or "",
        "current_answer": current_answer,
        "resume_summary": resume_summary or "",
        "cover_letter_summary": cover_letter_summary or "",
        "previous_answers": previous_answers or [],
    }

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CONSISTENCY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_completion_tokens=600,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        return {
            "level": "없음",
            "summary": f"(검증 일시 실패: {type(e).__name__})",
            "issues": [],
        }

    # 키 보정
    level = data.get("level") or "없음"
    if level not in {"없음", "낮음", "보통", "높음"}:
        level = "없음"
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    return {
        "level": level,
        "summary": data.get("summary") or "",
        "issues": issues,
    }


def check_consistency_for_session(
    qa_pairs: List[Dict[str, Any]],
    *,
    resume_summary: str = "",
    cover_letter_summary: str = "",
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    세션의 모든 Q/A 에 대해 일관성 검증을 순차 수행.
    qa_pairs: [{"question": "...", "answer": "...", "intent": "..."}, ...]
    반환: 같은 길이의 결과 리스트 (각 항목은 check_consistency 의 출력 + question_index)
    """
    results: List[Dict[str, Any]] = []
    previous: List[Dict[str, str]] = []
    for idx, qa in enumerate(qa_pairs or []):
        question = qa.get("question") or qa.get("text") or ""
        answer = qa.get("answer") or qa.get("transcript") or ""
        result = check_consistency(
            question, answer,
            resume_summary=resume_summary,
            cover_letter_summary=cover_letter_summary,
            previous_answers=previous,
            model=model,
            api_key=api_key,
        )
        result["question_index"] = idx
        results.append(result)
        previous.append({"question": question, "answer": answer})
    return results
