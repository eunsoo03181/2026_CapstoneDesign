"""
'심층 분석' — 면접 전체에 대해 더 깊은 markdown 보고서를 생성.

가장 비싼 모델(gpt-5.5)을 사용해 다음을 산출:
  - 한눈 요약 (3~5문장)
  - 답변 패턴 분석 (자주 빠지는 구성, 표현 습관 등)
  - 강점 톱5 / 약점 톱5 (각각 근거 인용)
  - 항목별 심층 코멘트 (공통 6항목)
  - 다음 면접까지 7일 액션 플랜
  - 가상의 인사담당자 종합 코멘트

결과는 markdown 텍스트 한 덩어리로 반환.
"""

import os
from typing import List, Dict, Any, Optional

from openai import OpenAI
from app.scoring.openai_usage import record_completion_usage


DEEP_MODEL_DEFAULT = "gpt-5.5"


SYSTEM_PROMPT = """당신은 30년 경력의 대기업 인사담당 임원입니다.
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
"""


def _format_questions_block(payload: Dict[str, Any]) -> str:
    """LLM 입력용 — 질문/답변/평가 결과를 텍스트로 직렬화."""
    out: List[str] = []
    for q in payload.get("questions", []):
        ev = q.get("evaluation") or {}
        cs = ev.get("common_scores") or {}
        custom = ev.get("custom_scores") or []
        score = ev.get("content_score")

        out.append(f"### Q{q.get('order_no', '?')} — {q.get('text','')}")
        if q.get("intent"):
            out.append(f"질문 의도: {q['intent']}")
        if q.get("evaluation_points"):
            out.append(f"평가 포인트: {q['evaluation_points']}")
        out.append("")
        out.append("답변:")
        out.append((q.get("transcript") or "(답변 없음)").strip())
        out.append("")

        if score is not None:
            out.append(f"채점: {score} / 80")
            if cs:
                out.append(
                    "공통: "
                    f"의도파악 {cs.get('question_understanding','-')}/9, "
                    f"구조 {cs.get('answer_structure','-')}/9, "
                    f"관련성 {cs.get('resume_job_relevance','-')}/13, "
                    f"구체성 {cs.get('specificity','-')}/9, "
                    f"논리 {cs.get('logic','-')}/6, "
                    f"간결 {cs.get('conciseness','-')}/4"
                )
            if custom:
                for cp in custom:
                    out.append(f"  - 맞춤기준 [{cp.get('point','')}] {cp.get('score','?')}/5")
            if ev.get("strengths"):
                out.append("강점: " + "; ".join(ev["strengths"]))
            if ev.get("improvements"):
                out.append("개선점: " + "; ".join(ev["improvements"]))
            if ev.get("content_feedback"):
                out.append("총평: " + ev["content_feedback"])
        out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


def generate_deep_analysis(
    session_payload: Dict[str, Any],
    model: str = DEEP_MODEL_DEFAULT,
) -> Dict[str, Any]:
    """
    session_payload — `_build_full_detail` 가 반환하는 형태 (questions, scores 등 포함).
    반환: {"markdown": str, "model": str}
    """
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    title = (session_payload.get("title") or "면접").strip()
    code  = session_payload.get("public_code") or ""
    final = session_payload.get("final_score_100")
    content = session_payload.get("content_score_80")
    nonverbal = session_payload.get("nonverbal_score_20")

    header = [
        f"제목: {title}  (코드 #{code})",
        f"최종 점수: {final if final is not None else '-'} / 100",
        f"답변 점수: {content if content is not None else '-'} / 80",
        f"비언어 점수: {nonverbal if nonverbal is not None else '-'} / 20",
    ]

    nv = session_payload.get("nonverbal_feedback") or {}
    if nv and not nv.get("error"):
        header.append("")
        header.append("비언어 요약:")
        for k in ("summary", "smile", "focus", "blink", "posture"):
            if nv.get(k):
                header.append(f"- {k}: {nv[k]}")

    user_block = "\n".join(header) + "\n\n" + _format_questions_block(session_payload)

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_block},
        ],
    )
    record_completion_usage(completion, endpoint="deep_analysis", model=model)
    md = (completion.choices[0].message.content or "").strip()

    return {"markdown": md, "model": model}
