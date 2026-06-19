"""
면접 답변 평가기 (OpenAI).

채점 구조 (질문당 80점):
  [공통 기준 — 50점, 답변 방식(HOW) 평가]
    1. 질문 의도 파악      9점
    2. 답변 구조성         9점
    3. 이력서/직무 관련성 13점
    4. 경험의 구체성       9점
    5. 논리성과 설득력     6점
    6. 표현의 간결성       4점

  [맞춤 기준 — 30점, 답변 내용(WHAT) 평가]
    질문별 evaluation_points 각각에 0~5점.
    합산을 30점 만점으로 정규화: (sum/(n*5)) × 30
"""

import os
import json
from typing import List, Dict, Optional, Tuple, Union

from openai import OpenAI
from app.scoring.openai_usage import record_completion_usage


# ---------- 점수 상한 ----------
COMMON_CRITERIA_MAX = {
    "question_understanding": 9,
    "answer_structure": 9,
    "resume_job_relevance": 13,
    "specificity": 9,
    "logic": 6,
    "conciseness": 4,
}
COMMON_MAX = sum(COMMON_CRITERIA_MAX.values())   # 50
CUSTOM_MAX = 30
TOTAL_MAX = COMMON_MAX + CUSTOM_MAX               # 80
CUSTOM_POINT_MAX = 5                              # eval_point 1개 만점

# 빈/환각 답변 placeholder — LLM 호출 없이 0점 처리
EMPTY_ANSWER_TEXT = "(내용 없음)"


def _is_empty_answer(text: str) -> bool:
    """답변이 비었거나 (내용 없음) placeholder 만 있으면 True."""
    s = (text or "").strip()
    return (not s) or (s == EMPTY_ANSWER_TEXT)


def _zero_score_result(question_id: str, eval_points: List[str]) -> Dict:
    """빈 답변에 대한 0점 응답 — LLM 호출 없음."""
    return {
        "question_id":     question_id,
        "common_scores":   {k: 0 for k in COMMON_CRITERIA_MAX},
        "common_subtotal": 0,
        "custom_scores":   [{"point": p, "score": 0} for p in eval_points],
        "custom_subtotal": 0,
        "content_score":   0,
        "strengths":       [],
        "improvements":    ["답변이 제출되지 않았습니다 — 다음 면접에선 어떤 답이라도 시도해보세요."],
        "content_feedback": "답변이 비어 있어 평가할 수 없습니다. (0점 처리)",
        "sample_answer":   "",
    }


# ---------- 시스템 프롬프트 ----------
SYSTEM_PROMPT = f"""너는 비대면 면접 연습 시스템의 답변 평가자이다.

주의사항:
- 지원자의 실제 합격/불합격을 판단하지 않는다.
- 인성, 외모, 나이, 성별 등 민감한 요소는 평가하지 않는다.
- 음성 인식 과정에서 일부 오타가 있을 수 있으므로, 명백한 STT 오류는 과도하게 감점하지 않는다.
- 점수는 너무 후하게 주지 말고, 부족한 부분을 구체적으로 제시한다.

평가는 두 부분으로 구성된다.

[A. 공통 기준] — {COMMON_MAX}점 만점. 답변 방식(HOW) 평가. 모든 질문에 동일 적용.
1. 질문 의도 파악:      {COMMON_CRITERIA_MAX['question_understanding']}점 - 질문 핵심을 정확히 이해했는가
2. 답변 구조성:         {COMMON_CRITERIA_MAX['answer_structure']}점 - 결론·근거·사례·마무리가 정리되어 있는가
3. 이력서/직무 관련성:  {COMMON_CRITERIA_MAX['resume_job_relevance']}점 - 답변이 이력서 경험과 지원 직무에 연결되는가
4. 경험의 구체성:       {COMMON_CRITERIA_MAX['specificity']}점 - 본인의 역할·행동·결과가 구체적으로 드러나는가
5. 논리성과 설득력:     {COMMON_CRITERIA_MAX['logic']}점 - 주장과 근거가 자연스럽게 연결되는가
6. 표현의 간결성:       {COMMON_CRITERIA_MAX['conciseness']}점 - 장황하지 않고 핵심을 전달했는가

[B. 맞춤 기준] — {CUSTOM_MAX}점 만점. 답변 내용(WHAT) 평가. 이 질문 고유 기준.
- 입력으로 받은 evaluation_points 각각에 0~{CUSTOM_POINT_MAX}점 부여
- 시스템이 (점수 합 / 항목 수 / {CUSTOM_POINT_MAX}) × {CUSTOM_MAX} 로 정규화하여 합산
- 답변이 그 포인트를 직접 다루지 않으면 0점, 부분적이면 1~3점, 충실하면 4~5점

총점 = 공통 + 맞춤 = {TOTAL_MAX}점

출력은 반드시 다음 JSON 스키마로만 응답한다:
{{
  "question_id": "",
  "common_scores": {{
    "question_understanding": 0,
    "answer_structure": 0,
    "resume_job_relevance": 0,
    "specificity": 0,
    "logic": 0,
    "conciseness": 0
  }},
  "custom_scores": [
    {{ "point": "<입력으로 받은 평가 포인트 텍스트>", "score": 0 }}
  ],
  "strengths": [""],
  "improvements": [""],
  "content_feedback": "",
  "sample_answer": ""
}}

출력 시 주의:
- common_scores 각 항목은 위 상한을 초과하지 않는다.
- custom_scores 의 score 는 0~{CUSTOM_POINT_MAX} 정수.
- custom_scores 항목은 입력으로 받은 evaluation_points 와 같은 개수, 같은 순서로 작성한다.
- common_subtotal / custom_subtotal / content_score 는 시스템이 자동 계산하므로 출력하지 않는다.
- strengths 는 2개 이상.
- improvements 는 2개 이상.
- sample_answer 는 지원자의 이력서와 질문 의도에 맞게 개선된 답변 예시.
- 모든 텍스트는 한국어 존댓말로 작성한다."""


# ---------- 메시지 빌더 ----------
def _format_eval_points(points: List[str]) -> str:
    if not points:
        return "(명시되지 않음 — 공통 기준만으로 평가)"
    return "\n".join(f"  {i+1}. {p}" for i, p in enumerate(points))


def _build_user_message(
    question_id: str,
    question: str,
    intent: str,
    evaluation_points: List[str],
    resume_summary: str,
    transcript: str,
) -> str:
    return (
        f"질문 ID: {question_id}\n\n"
        f"질문:\n{question}\n\n"
        f"질문 의도:\n{intent or '(명시되지 않음 — 질문 자체에서 추론)'}\n\n"
        f"평가 포인트(맞춤 기준):\n{_format_eval_points(evaluation_points)}\n\n"
        f"이력서 요약:\n{resume_summary}\n\n"
        f"지원자 답변:\n{transcript}"
    )


# ---------- 후처리/검증 ----------
def _validate_and_fix(result: Dict, question_id: str, eval_points: List[str]) -> Dict:
    """LLM 응답의 점수 상한·합계·필수 필드를 보정."""
    result.setdefault("question_id", question_id)

    # 1) 공통 점수 클램프
    cs = result.get("common_scores", {}) or {}
    for k, max_v in COMMON_CRITERIA_MAX.items():
        v = int(cs.get(k, 0) or 0)
        cs[k] = max(0, min(v, max_v))
    result["common_scores"] = cs
    common_subtotal = sum(cs.values())

    # 2) 맞춤 점수 정렬 & 클램프
    raw_custom = result.get("custom_scores", []) or []
    fixed_custom = []
    for i, point_text in enumerate(eval_points):
        # 모델이 같은 순서로 줬다고 가정. 부족하면 0점 채움.
        item = raw_custom[i] if i < len(raw_custom) and isinstance(raw_custom[i], dict) else {}
        score = int(item.get("score", 0) or 0)
        score = max(0, min(score, CUSTOM_POINT_MAX))
        fixed_custom.append({"point": point_text, "score": score})
    result["custom_scores"] = fixed_custom

    # 3) 맞춤 정규화
    if eval_points:
        raw_sum = sum(c["score"] for c in fixed_custom)
        custom_subtotal = (raw_sum / (len(eval_points) * CUSTOM_POINT_MAX)) * CUSTOM_MAX
    else:
        custom_subtotal = 0.0

    result["common_subtotal"] = common_subtotal           # 0~50
    result["custom_subtotal"] = round(custom_subtotal, 2)  # 0~30
    result["content_score"] = round(common_subtotal + custom_subtotal, 2)  # 0~80

    # 4) strengths / improvements 최소 2개 보장
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


# ---------- 단일 평가 ----------
def evaluate_answer(
    question: str,
    transcript: str,
    resume_summary: str,
    intent: str = "",
    evaluation_points: Optional[List[str]] = None,
    question_id: str = "Q1",
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> Dict:
    """단일 (질문, 답변) 평가."""
    eval_points = list(evaluation_points or [])

    # 빈/환각 답변 short-circuit — LLM 호출 없이 0점 처리 (토큰 절약 + 일관성)
    if _is_empty_answer(transcript):
        return _zero_score_result(question_id, eval_points)

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    user_msg = _build_user_message(
        question_id=question_id,
        question=question,
        intent=intent,
        evaluation_points=eval_points,
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
    record_completion_usage(res, endpoint="answer_evaluator", model=model)
    raw = res.choices[0].message.content
    data = json.loads(raw)
    return _validate_and_fix(data, question_id, eval_points)


# ---------- 집계 ----------
def _aggregate(items: List[Dict]) -> Dict:
    n = len(items)
    if n == 0:
        return {
            "num_questions": 0,
            "max_per_question": TOTAL_MAX,
            "max_total": 0,
            "total_score": 0,
            "average_score": 0.0,
            "percentage": 0.0,
            "common_total": 0.0,
            "common_average": 0.0,
            "custom_total": 0.0,
            "custom_average": 0.0,
            "common_criteria_totals": {k: 0 for k in COMMON_CRITERIA_MAX},
            "common_criteria_averages": {k: 0.0 for k in COMMON_CRITERIA_MAX},
            "per_question": [],
        }

    total = sum(it["content_score"] for it in items)
    max_total = TOTAL_MAX * n
    common_sum = sum(it.get("common_subtotal", 0) for it in items)
    custom_sum = sum(it.get("custom_subtotal", 0) for it in items)

    common_totals = {k: 0 for k in COMMON_CRITERIA_MAX}
    for it in items:
        for k in COMMON_CRITERIA_MAX:
            common_totals[k] += int(it.get("common_scores", {}).get(k, 0))

    return {
        "num_questions": n,
        "max_per_question": TOTAL_MAX,
        "max_total": max_total,
        "total_score": round(total, 2),
        "average_score": round(total / n, 2),
        "percentage": round(total / max_total * 100, 2),
        "common_total": round(common_sum, 2),
        "common_average": round(common_sum / n, 2),
        "custom_total": round(custom_sum, 2),
        "custom_average": round(custom_sum / n, 2),
        "common_criteria_totals": common_totals,
        "common_criteria_averages": {
            k: round(v / n, 2) for k, v in common_totals.items()
        },
        "per_question": [
            {
                "question_id": it["question_id"],
                "content_score": it["content_score"],
                "common_subtotal": it.get("common_subtotal", 0),
                "custom_subtotal": it.get("custom_subtotal", 0),
            }
            for it in items
        ],
    }


# ---------- 세션 평가 ----------
QuestionLike = Union[str, Dict]


def evaluate_session(
    qa_pairs: List[Tuple[QuestionLike, str]],
    resume_summary: str,
    model: str = "gpt-4o-mini",
) -> Dict:
    """
    여러 (질문, 답변) 쌍을 일괄 평가.

    질문은 dict 형식 권장:
      {"question_id": "...", "question": "...", "intent": "...", "evaluation_points": [...]}

    legacy 호환: 질문이 단순 문자열이어도 동작 (intent/eval_points 없음 → 공통 50점만).
    """
    items: List[Dict] = []
    for i, (q, a) in enumerate(qa_pairs, start=1):
        if isinstance(q, dict):
            question_text = q.get("question", "")
            intent = q.get("intent", "")
            eval_points = list(q.get("evaluation_points") or [])
            qid = q.get("question_id", f"Q{i}")
        else:
            question_text = q
            intent = ""
            eval_points = []
            qid = f"Q{i}"

        result = evaluate_answer(
            question=question_text,
            transcript=a,
            resume_summary=resume_summary,
            intent=intent,
            evaluation_points=eval_points,
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
            {
                "question_id": "P01",
                "question": "Django 인턴 기간 동안 본인이 주도적으로 해결한 문제는 무엇이고, 어떤 결과를 얻었나요?",
                "intent": "주도적 문제 해결 능력과 결과 측정 능력 평가",
                "evaluation_points": [
                    "해결한 문제를 구체적인 숫자나 지표로 정의했는가",
                    "본인의 행동(타인과 구분되는 기여)이 명확한가",
                    "단계적 해결 과정이 논리적으로 드러나는가",
                    "결과를 측정 가능한 형태로 제시했는가",
                ],
            },
            "음... 그냥 API 만들었고요, 버그도 좀 고쳤습니다. 팀에서 잘 했다고 했어요.",
        ),
        (
            {
                "question_id": "P02",
                "question": "졸업작품 졸음 감지 시스템에서 정확도를 높이기 위해 어떤 시도를 했나요?",
                "intent": "기술적 문제 진단과 정량적 검증 능력 평가",
                "evaluation_points": [
                    "고정 임계값의 한계를 정확히 진단했는가",
                    "개인 차이를 정량적으로 측정했는가",
                    "단계적 개선 과정이 드러나는가",
                    "검증 결과를 숫자로 표현했는가",
                ],
            },
            "EAR 임계값을 0.165로 잡고 사람마다 다른 눈 크기를 보정하기 위해 5초간 베이스라인을 측정했습니다. "
            "그리고 PERCLOS 75프레임 윈도우를 추가해 단순 깜빡임과 졸음을 분리했고, "
            "MAR 기반 하품 카운트를 더해 종합 판정으로 오탐을 30% 줄였습니다.",
        ),
    ]

    result = evaluate_session(qa_pairs, resume_summary=sample_resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))
