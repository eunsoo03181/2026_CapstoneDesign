"""
OpenAI / Whisper 호출 토큰 사용량을 DB(token_usage 테이블)에 적재.

- contextvars 로 호출 스레드/태스크에 user_id, session_id 를 박아두면,
  각 LLM 모듈은 응답 직후 record_completion_usage(...) 1줄만 호출하면 자동 적재됨.
- DB 호출 실패해도 본 면접 흐름은 영향 X (silent fail).
- 단가표는 호출 시점 스냅샷 — 단가 변동 후 과거 row 는 재계산 안 함.

사용 예 (호출자 — main.py)
  from app.scoring.openai_usage import set_usage_context
  set_usage_context(user_id=user.id, session_id=sid)

사용 예 (LLM 모듈)
  from app.scoring.openai_usage import record_completion_usage
  resp = client.chat.completions.create(...)
  record_completion_usage(resp, endpoint="answer_evaluator", model=model)
"""

import contextvars
import logging
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger(__name__)


# ============================================================
# 단가표 (USD per 1M tokens 또는 per minute) — 호출 시점 스냅샷용
# 실제 가격은 OpenAI 공식 페이지에 맞춰 주기적 업데이트 필요.
# ============================================================
PRICING_PER_1M_TOKENS = {
    # 모델명: (input_usd_per_1M, output_usd_per_1M)
    "gpt-4o":         (2.50,  10.00),
    "gpt-4o-mini":    (0.15,  0.60),
    "gpt-4.1":        (2.00,  8.00),
    "gpt-4.1-mini":   (0.40,  1.60),
    "gpt-5":          (5.00,  15.00),
    "gpt-5-mini":     (0.30,  1.20),
    "gpt-5.5":        (8.00,  24.00),
    # 정확한 매칭 안 되면 _DEFAULT 사용
    "_DEFAULT":       (1.00,  4.00),
}

# Whisper STT — minute 단위
WHISPER_USD_PER_MINUTE = 0.006


def _resolve_pricing(model: str) -> tuple[float, float]:
    """모델 이름 prefix 매칭으로 단가 조회. 못 찾으면 _DEFAULT."""
    m = (model or "").strip().lower()
    if m in PRICING_PER_1M_TOKENS:
        return PRICING_PER_1M_TOKENS[m]
    # prefix 매칭 (예: "gpt-4o-mini-2024-07-18" → "gpt-4o-mini")
    for key, price in PRICING_PER_1M_TOKENS.items():
        if key != "_DEFAULT" and m.startswith(key):
            return price
    return PRICING_PER_1M_TOKENS["_DEFAULT"]


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = _resolve_pricing(model)
    cost = (prompt_tokens / 1_000_000) * in_price + (completion_tokens / 1_000_000) * out_price
    return round(cost, 6)


def estimate_whisper_cost_usd(duration_sec: float) -> float:
    minutes = max(0.0, float(duration_sec or 0)) / 60.0
    return round(minutes * WHISPER_USD_PER_MINUTE, 6)


# ============================================================
# 호출 context — contextvars 로 비동기 흐름 전반에 전파
# ============================================================
_user_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "token_usage_user_id", default=None,
)
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "token_usage_session_id", default=None,
)


def set_usage_context(*, user_id: Optional[str] = None, session_id: Optional[str] = None) -> None:
    """현재 컨텍스트(스레드/태스크)에 user_id, session_id 박아둠.
    이후 같은 컨텍스트의 LLM 호출은 자동으로 이 식별자를 사용."""
    if user_id is not None:
        _user_id_var.set(user_id)
    if session_id is not None:
        _session_id_var.set(session_id)


def clear_usage_context() -> None:
    _user_id_var.set(None)
    _session_id_var.set(None)


def _current_user_id() -> Optional[str]:
    try:
        return _user_id_var.get()
    except LookupError:
        return None


def _current_session_id() -> Optional[str]:
    try:
        return _session_id_var.get()
    except LookupError:
        return None


# ============================================================
# 적재 — chat completions
# ============================================================
def record_completion_usage(
    resp: Any,
    *,
    endpoint: str,
    model: str,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """ChatCompletion 응답의 usage 필드를 DB token_usage 테이블에 1 row 적재.

    응답에 usage 가 없거나 DB 호출 실패 시 silent fail — 본 흐름엔 영향 X.
    user_id/session_id 가 명시되지 않으면 contextvars 에서 가져옴.
    """
    try:
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage")
        if not usage:
            return

        prompt_tokens = int(_get(usage, "prompt_tokens") or 0)
        completion_tokens = int(_get(usage, "completion_tokens") or 0)
        total_tokens = int(_get(usage, "total_tokens") or (prompt_tokens + completion_tokens))

        cost = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        _insert_row(
            endpoint=endpoint,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            audio_seconds=None,
            cost_usd=cost,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as e:
        log.debug("record_completion_usage 실패 (무시): %s", e)


def record_whisper_usage(
    duration_sec: float,
    *,
    endpoint: str = "whisper_stt",
    model: str = "whisper-1",
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Whisper STT 호출 — duration 기반 과금. token 컬럼은 0."""
    try:
        cost = estimate_whisper_cost_usd(duration_sec)
        _insert_row(
            endpoint=endpoint,
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            audio_seconds=float(duration_sec or 0),
            cost_usd=cost,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception as e:
        log.debug("record_whisper_usage 실패 (무시): %s", e)


def _get(obj: Any, key: str) -> Any:
    """dict/pydantic/openai 객체 어디서든 키 추출."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _insert_row(**kwargs) -> None:
    """SessionLocal 로 새 세션 열어 1 row insert + commit.
    호출자의 db.Session 을 빌리지 않는 이유: LLM 모듈은 db 인자가 없는 경우가 많고,
    토큰 로그가 본 트랜잭션과 같이 롤백되면 안 되므로 별도 트랜잭션이 안전.
    """
    # 늦은 import — 순환 의존 방지
    from db import SessionLocal, TokenUsage
    from db.utils import gen_uuid

    user_id = kwargs.pop("user_id", None) or _current_user_id()
    session_id = kwargs.pop("session_id", None) or _current_session_id()

    sess = SessionLocal()
    try:
        row = TokenUsage(
            id=gen_uuid(),
            user_id=user_id,
            session_id=session_id,
            created_at=datetime.utcnow(),
            **kwargs,
        )
        sess.add(row)
        sess.commit()
    except Exception as e:
        log.debug("token_usage insert 실패: %s", e)
        try:
            sess.rollback()
        except Exception:
            pass
    finally:
        try:
            sess.close()
        except Exception:
            pass
