"""
company_research.py

사용자가 입력한 회사·직무 정보(텍스트)를 받아 면접에 활용할 수 있는
구조화된 company_job_summary 를 생성하고, 다른 프롬프트에 끼워 넣을 수 있는
컴팩트 텍스트 블록으로 변환한다.

웹 크롤링은 하지 않는다 — 사용자가 직접 붙여넣은 채용공고/JD/회사 설명을 입력으로 받음.

호출 측 (main.py) 흐름:
  1. 사용자가 회사명·직무명·회사·직무 텍스트(JD 등) 를 입력
  2. summarize_company_job_from_text_async() 호출 → company_job_summary JSON
  3. format_company_block() 으로 텍스트 블록 추출
  4. 이력서 요약 + 회사 컨텍스트 블록을 concat 해 question_generator·pressure_generator 에 전달
"""

import os
import json
from typing import Optional, List, Dict, Any

from openai import AsyncOpenAI
from app.scoring.openai_usage import record_completion_usage

from app.company.company_research_prompts import (
    COMPANY_JOB_SUMMARY_FROM_TEXT_PROMPT,
    COMPANY_JOB_BASED_QUESTION_PROMPT,
    COMPANY_JOB_RESEARCH_VALIDATION_PROMPT,
    COMPANY_RESEARCH_FROM_NAME_PROMPT,
    EMPTY_COMPANY_JOB_SUMMARY,
)


ADVANCED_MODELS = {"gpt-4o", "gpt-5.4", "gpt-5.5"}


def _is_advanced(model: Optional[str]) -> bool:
    return (model or "") in ADVANCED_MODELS


# 텍스트 입력 최대 크기 (토큰 한도 보호)
TEXT_HARD_CAP = 12000


async def summarize_company_job_from_text_async(
    *,
    model: str,
    company_name: str,
    job_title: str,
    pasted_text: str,
    candidate_summary: str = "",
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    사용자가 붙여넣은 채용공고/JD/회사 설명 텍스트를 받아 company_job_summary JSON 생성.
    실패 시 None.
    """
    if not pasted_text or not pasted_text.strip():
        return None

    src = pasted_text[:TEXT_HARD_CAP]
    user_payload = {
        "company_name":     company_name or "확인 불가",
        "job_title":        job_title or "확인 불가",
        "pasted_text":      src,
        "candidate_summary": candidate_summary or "",
    }
    user_prompt = (
        "[입력 자료]\n"
        + json.dumps(user_payload, ensure_ascii=False, indent=2)
    )

    try:
        client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COMPANY_JOB_SUMMARY_FROM_TEXT_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_completion_tokens=2000 if _is_advanced(model) else 1400,
        )
        record_completion_usage(resp, endpoint='company_research', model=model)
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw) or {}
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    # 기본 형태 보장 — 빈 필드는 빈 값으로 채움
    out = dict(EMPTY_COMPANY_JOB_SUMMARY)
    out["company_name"] = str(data.get("company_name") or company_name or "").strip()
    out["job_title"]    = str(data.get("job_title")    or job_title    or "").strip()
    cjs_src = data.get("company_job_summary") or {}
    cjs_default = dict(EMPTY_COMPANY_JOB_SUMMARY["company_job_summary"])
    for k, default_val in cjs_default.items():
        v = cjs_src.get(k, default_val)
        # 리스트 필드에 문자열이 오면 단일 항목 리스트로 감싸기
        if isinstance(default_val, list) and not isinstance(v, list):
            v = [v] if v else []
        cjs_default[k] = v
    out["company_job_summary"] = cjs_default
    out["sources"]     = data.get("sources") or []
    out["limitations"] = data.get("limitations") or []
    return out


async def research_company_from_name_async(
    *,
    model: str,
    company_name: str,
    job_title: str,
    candidate_summary: str = "",
    api_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    회사명·직무명만 받아 company_job_summary JSON 자동 생성.

    실시간 웹 검색 없이 모델 학습 지식 기반으로 작성.
    시점 의존 정보·미확인 필드는 "확인 불가" 로 표시.

    회사명/직무명 둘 다 비어있으면 None.
    """
    cname = (company_name or "").strip()
    jtitle = (job_title or "").strip()
    if not cname and not jtitle:
        return None

    user_payload = {
        "company_name":      cname or "확인 불가",
        "job_title":         jtitle or "확인 불가",
        "candidate_summary": candidate_summary or "",
    }
    user_prompt = (
        "[리서치 대상]\n"
        + json.dumps(user_payload, ensure_ascii=False, indent=2)
    )

    try:
        client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COMPANY_RESEARCH_FROM_NAME_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_completion_tokens=2000 if _is_advanced(model) else 1400,
        )
        record_completion_usage(resp, endpoint='company_research', model=model)
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw) or {}
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # summarize_company_job_from_text_async 와 동일한 정규화 로직
    out = dict(EMPTY_COMPANY_JOB_SUMMARY)
    out["company_name"] = str(data.get("company_name") or cname or "").strip()
    out["job_title"]    = str(data.get("job_title")    or jtitle or "").strip()
    cjs_src = data.get("company_job_summary") or {}
    cjs_default = dict(EMPTY_COMPANY_JOB_SUMMARY["company_job_summary"])
    for k, default_val in cjs_default.items():
        v = cjs_src.get(k, default_val)
        if isinstance(default_val, list) and not isinstance(v, list):
            v = [v] if v else []
        cjs_default[k] = v
    out["company_job_summary"] = cjs_default
    out["sources"]     = data.get("sources") or [{
        "title": "모델 학습 지식 기반",
        "source_url": "확인 불가",
        "source_type": "model_training_knowledge",
        "used_for": ["auto-research"],
    }]
    out["limitations"] = data.get("limitations") or [
        "실시간 웹 검색 없이 학습 지식 기반 — 최신·세부 정보는 부정확할 수 있음"
    ]
    return out


def format_company_block(summary: Optional[Dict[str, Any]], max_chars: int = 1800) -> str:
    """
    company_job_summary 를 다른 프롬프트에 끼워 넣을 컴팩트 한국어 텍스트 블록으로 변환.

    면접 질문 생성기 (question_generator / pressure_generator / followup_generator)
    의 candidate_summary 에 추가로 연결해 사용한다.
    """
    if not summary:
        return ""

    cjs = (summary.get("company_job_summary") or {})
    parts: List[str] = []
    cname = (summary.get("company_name") or "").strip()
    jtitle = (summary.get("job_title") or "").strip()
    if cname or jtitle:
        parts.append(f"회사: {cname or '확인 불가'} / 직무: {jtitle or '확인 불가'}")

    def _add_text(label: str, val: Any):
        if not val:
            return
        if isinstance(val, list):
            val = ", ".join(str(x).strip() for x in val if str(x).strip())
        v = str(val).strip()
        if v and v != "확인 불가":
            parts.append(f"- {label}: {v}")

    _add_text("회사 개요",             cjs.get("company_overview"))
    _add_text("직무 연관성",            cjs.get("business_relevance_to_job"))
    _add_text("주요 사업",              cjs.get("main_business"))
    _add_text("최근 전략",              cjs.get("recent_strategy"))
    _add_text("직무 역할",              cjs.get("job_role"))
    _add_text("필요 역량",              cjs.get("required_competencies"))
    _add_text("필요 지식",              cjs.get("required_knowledge"))
    _add_text("관련 기술",              cjs.get("required_technologies"))
    _add_text("업무 환경",              cjs.get("work_context"))
    _add_text("핵심가치/인재상",         cjs.get("core_values"))
    _add_text("면접 키워드",             cjs.get("interview_keywords"))

    seeds = cjs.get("question_seed_points") or []
    if seeds:
        seed_texts = []
        for s in seeds[:6]:
            t = (s.get("topic") if isinstance(s, dict) else str(s)) or ""
            t = t.strip()
            if t:
                seed_texts.append(t)
        if seed_texts:
            parts.append("- 면접 시드: " + " / ".join(seed_texts))

    pressures = cjs.get("pressure_points") or []
    if pressures:
        ps_texts = []
        for p in pressures[:5]:
            t = (p.get("point") if isinstance(p, dict) else str(p)) or ""
            t = t.strip()
            if t:
                ps_texts.append(t)
        if ps_texts:
            parts.append("- 검증 포인트: " + " / ".join(ps_texts))

    block = "\n".join(parts).strip()
    if len(block) > max_chars:
        block = block[: max_chars - 1] + "…"
    return block


def merge_candidate_and_company(
    resume_summary: str,
    company_block: str,
) -> str:
    """
    이력서 요약 + 회사·직무 컨텍스트 블록을 면접 질문 생성기에 넘길 단일 텍스트로 합침.
    회사 정보가 없으면 이력서 요약 그대로 반환.
    """
    rs = (resume_summary or "").strip()
    cb = (company_block or "").strip()
    if not cb:
        return rs
    if not rs:
        return f"[지원 회사·직무 컨텍스트]\n{cb}"
    return f"{rs}\n\n[지원 회사·직무 컨텍스트]\n{cb}"


async def generate_company_questions_async(
    *,
    model: str,
    company_summary: Dict[str, Any],
    candidate_summary: str,
    n: int = 3,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    회사·직무 정보 기반 맞춤 질문 생성.
    실패 시 빈 리스트. (현재 메인 파이프라인에서 직접 호출하진 않지만,
    추후 'company-aware question pack' 으로 확장 가능.)
    """
    if n <= 0 or not company_summary:
        return []
    user_payload = {
        "company_summary":   company_summary,
        "candidate_summary": candidate_summary or "",
        "n":                 int(n),
    }
    try:
        client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COMPANY_JOB_BASED_QUESTION_PROMPT},
                {"role": "user",   "content": json.dumps(user_payload, ensure_ascii=False, indent=2)},
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
            max_completion_tokens=1500 if _is_advanced(model) else 900,
        )
        record_completion_usage(resp, endpoint='company_research', model=model)
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return []
    items = data.get("questions") or []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        q = str(it.get("question") or "").strip()
        if not q:
            continue
        out.append({"question": q, "raw": it})
    return out
