"""
면접 답변 평가기 (OpenAI).

(질문, 답변) 쌍과 이력서 요약을 받아 80점 만점으로 평가하고
JSON 결과를 반환한다.

평가 기준 (총 80점):
  1. 질문 의도 파악      : 15점
  2. 답변 구조성         : 15점
  3. 이력서/직무 관련성  : 20점
  4. 경험의 구체성       : 15점
  5. 논리성과 설득력     : 10점
  6. 표현의 간결성       :  5점
"""

import os
import json
from typing import List, Dict, Optional, Tuple

from openai import OpenAI


SYSTEM_PROMPT = """너는 비대면 면접 연습 시스템의 답변 평가자이다.

주의사항:
- 지원자의 실제 합격/불합격을 판단하지 않는다.
- 인성, 외모, 나이, 성별 등 민감한 요소는 평가하지 않는다.
- 제공된 질문, 질문 의도, 평가 포인트, 이력서 요약, 답변 텍스트를 기준으로 답변 품질만 평가한다.
- 점수는 80점 만점으로 산출한다.
- 점수는 너무 후하게 주지 말고, 부족한 부분을 구체적으로 제시한다.
- 음성 인식 과정에서 일부 오타가 있을 수 있으므로, 명백한 STT 오류는 과도하게 감점하지 않는다.

평가 기준:
1. 질문 의도 파악: 15점 - 질문에서 묻는 핵심을 정확히 이해하고 답했는가
2. 답변 구조성: 15점 - 결론, 근거, 사례, 마무리가 정리되어 있는가
3. 이력서/직무 관련성: 20점 - 답변이 이력서 경험과 지원 직무에 연결되는가
4. 경험의 구체성: 15점 - 본인의 역할, 행동, 결과가 구체적으로 드러나는가
5. 논리성과 설득력: 10점 - 주장과 근거가 자연스럽게 연결되는가
6. 표현의 간결성: 5점 - 장황하지 않고 핵심을 전달했는가

출력 형식:
아래 스키마에 정확히 맞춘 JSON 객체로만 응답한다.
{
  "question_id": "",
  "content_score": 0,
  "criteria_scores": {
    "question_understanding": 0,
    "answer_structure": 0,
    "resume_job_relevance": 0,
    "specificity": 0,
    "logic": 0,
    "conciseness": 0
  },
  "strengths": [""],
  "improvements": [""],
  "content_feedback": "",
  "sample_answer": ""
}

출력 시 주의사항:
- content_score는 80점을 초과할 수 없다.
- 각 항목별 점수의 합이 content_score와 일치해야 한다.
  (question_understanding ≤ 15, answer_structure ≤ 15, resume_job_relevance ≤ 20,
   specificity ≤ 15, logic ≤ 10, conciseness ≤ 5)
- strengths는 2개 이상 작성한다.
- improvements는 2개 이상 작성한다.
- sample_answer는 지원자의 이력서와 질문 의도에 맞게 개선된 답변 예시로 작성한다.
- 모든 텍스트는 한국어 존댓말로 작성한다."""


CRITERIA_MAX = {
    "question_understanding": 15,
    "answer_structure": 15,
    "resume_job_relevance": 20,
    "specificity": 15,
    "logic": 10,
    "conciseness": 5,
}


def _build_user_message(
    question_id: str,
    question: str,
    intent: str,
    evaluation_points: str,
    resume_summary: str,
    transcript: str,
) -> str:
    return (
        f"질문 ID: {question_id}\n\n"
        f"질문:\n{question}\n\n"
        f"질문 의도:\n{intent}\n\n"
        f"평가 포인트:\n{evaluation_points}\n\n"
        f"이력서 요약:\n{resume_summary}\n\n"
        f"지원자 답변:\n{transcript}"
    )


def _validate_and_fix(result: Dict, question_id: str) -> Dict:
    """모델 출력의 점수 합/상한을 검증·보정한다."""
    result.setdefault("question_id", question_id)
    cs = result.get("criteria_scores", {})

    # 항목별 상한 클램프
    for k, max_v in CRITERIA_MAX.items():
        v = int(cs.get(k, 0) or 0)
        cs[k] = max(0, min(v, max_v))
    result["criteria_scores"] = cs

    # 합계 = content_score 강제 정합
    total = sum(cs.values())
    result["content_score"] = min(total, 80)

    # strengths / improvements 최소 2개 보장
    for key in ("strengths", "improvements"):
        items = result.get(key) or []
        if not isinstance(items, list):
            items = [str(items)]
        if len(items) < 2:
            items = items + ["(추가 의견 없음)"] * (2 - len(items))
        result[key] = items

    result.setdefault("content_feedback", "")
    result.setdefault("sample_answer", "")
    return result


def evaluate_answer(
    question: str,
    transcript: str,
    resume_summary: str,
    intent: str = "",
    evaluation_points: str = "",
    question_id: str = "Q1",
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> Dict:
    """단일 (질문, 답변) 평가."""
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    user_msg = _build_user_message(
        question_id=question_id,
        question=question,
        intent=intent or "(명시되지 않음 — 질문 자체에서 의도를 추론하시오)",
        evaluation_points=evaluation_points or "(명시되지 않음 — 평가 기준에 근거해 판단)",
        resume_summary=resume_summary,
        transcript=transcript,
    )

    res = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = res.choices[0].message.content
    data = json.loads(raw)
    return _validate_and_fix(data, question_id)


def _aggregate(items: List[Dict]) -> Dict:
    """
    개별 평가 결과들을 받아 종합 지표를 계산.

    반환 키:
      - num_questions     : 평가한 질문 수 N
      - max_per_question  : 80 (질문당 만점)
      - max_total         : 80 * N
      - total_score       : 모든 질문 content_score 합계 (0 ~ max_total)
      - average_score     : N 으로 나눈 평균 (0 ~ 80)
      - percentage        : total_score / max_total * 100  (0 ~ 100)
      - criteria_totals   : 항목별 합계 (예: question_understanding 합)
      - criteria_averages : 항목별 평균 (질문당 평균 점수)
      - per_question      : [{question_id, content_score}] 간단 요약
    """
    n = len(items)
    if n == 0:
        return {
            "num_questions": 0,
            "max_per_question": 80,
            "max_total": 0,
            "total_score": 0,
            "average_score": 0.0,
            "percentage": 0.0,
            "criteria_totals": {k: 0 for k in CRITERIA_MAX},
            "criteria_averages": {k: 0.0 for k in CRITERIA_MAX},
            "per_question": [],
        }

    total = sum(it["content_score"] for it in items)
    max_total = 80 * n

    criteria_totals = {k: 0 for k in CRITERIA_MAX}
    for it in items:
        for k in CRITERIA_MAX:
            criteria_totals[k] += int(it.get("criteria_scores", {}).get(k, 0))

    criteria_averages = {k: round(v / n, 2) for k, v in criteria_totals.items()}

    return {
        "num_questions": n,
        "max_per_question": 80,
        "max_total": max_total,
        "total_score": total,
        "average_score": round(total / n, 2),
        "percentage": round(total / max_total * 100, 2),
        "criteria_totals": criteria_totals,
        "criteria_averages": criteria_averages,
        "per_question": [
            {"question_id": it["question_id"], "content_score": it["content_score"]}
            for it in items
        ],
    }


def evaluate_session(
    qa_pairs: List[Tuple[str, str]],
    resume_summary: str,
    intents: Optional[List[str]] = None,
    eval_points: Optional[List[str]] = None,
    model: str = "gpt-4o-mini",
) -> Dict:
    """
    여러 (질문, 답변) 쌍을 일괄 평가.

    반환:
      {
        "items":     [질문별 평가 dict, ...],     # 질문당 80점 만점
        "summary":   {집계 지표(_aggregate 참조)}  # 합계/평균/항목별 평균/환산
      }
    """
    items: List[Dict] = []
    for i, (q, a) in enumerate(qa_pairs, start=1):
        qid = f"Q{i}"
        intent = intents[i - 1] if intents and i - 1 < len(intents) else ""
        ep = eval_points[i - 1] if eval_points and i - 1 < len(eval_points) else ""
        result = evaluate_answer(
            question=q,
            transcript=a,
            resume_summary=resume_summary,
            intent=intent,
            evaluation_points=ep,
            question_id=qid,
            model=model,
        )
        items.append(result)

    return {"items": items, "summary": _aggregate(items)}


if __name__ == "__main__":
    sample_resume = (
        "전자공학과 졸업, ABC 스타트업 백엔드 인턴 6개월(Python/Django), "
        "졸업작품: 졸음 감지 시스템(OpenCV, MediaPipe)"
    )

    qa_pairs = [
        (
            "Django 인턴 기간 동안 본인이 주도적으로 해결한 문제는 무엇이고, 어떤 결과를 얻었나요?",
            "음... 그냥 API 만들었고요, 버그도 좀 고쳤습니다. 팀에서 잘 했다고 했어요.",
        ),
        (
            "졸업작품인 졸음 감지 시스템에서 정확도를 높이기 위해 어떤 시도를 했나요?",
            "EAR 임계값을 0.165로 잡고 사람마다 다른 눈 크기를 보정하기 위해 5초간 베이스라인을 측정했습니다. "
            "그리고 PERCLOS 75프레임 윈도우를 추가해 단순 깜빡임과 졸음을 분리했고, "
            "MAR 기반 하품 카운트를 더해 종합 판정으로 오탐을 줄였습니다.",
        ),
    ]

    result = evaluate_session(qa_pairs, resume_summary=sample_resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))
