"""
음성 비언어 평가 (10점 만점) — 말 속도 / 침묵 / 반복어·filler / 전달 안정성.

비대면 면접에서 말하기 자체의 안정감을 평가. 거짓말·긴장 같은 주관 해석 X.
오로지 Whisper STT 의 텍스트 + 단어별 timestamp 만으로 객관 수치를 계산.

원안: 사용자 제공 voice_nonverbal_eval.py
변경:
  - 메인 엔트리는 evaluate_voice_nonverbal_from_transcript(text, words)
    (STT 는 speech_to_text.transcribe_with_whisper_words 가 담당, 호출 중복 방지)
  - 세션 단위 집계 helper aggregate_voice_evals() 추가
"""

import re
from typing import Any, Dict, List, Optional


FILLER_WORDS = [
    "음", "어", "아", "그", "그게", "이제", "약간",
    "뭔가", "그러니까", "네", "저기", "음...", "어...",
]


def _normalize_word(word: str) -> str:
    return re.sub(r"[^\w가-힣]", "", word or "").strip()


# ============================================================
# 1) 원시 지표 계산
# ============================================================
def calculate_speech_rate(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not words:
        return {"duration_sec": 0.0, "word_count": 0, "words_per_minute": 0.0}
    start_time = float(words[0].get("start", 0))
    end_time = float(words[-1].get("end", 0))
    duration_sec = max(end_time - start_time, 1.0)
    word_count = len(words)
    wpm = word_count / duration_sec * 60
    return {
        "duration_sec": round(duration_sec, 2),
        "word_count": word_count,
        "words_per_minute": round(wpm, 2),
    }


def analyze_pauses(
    words: List[Dict[str, Any]],
    pause_threshold: float = 1.5,
) -> Dict[str, Any]:
    pauses = []
    for i in range(1, len(words)):
        prev_end = words[i - 1].get("end")
        cur_start = words[i].get("start")
        if prev_end is None or cur_start is None:
            continue
        gap = float(cur_start) - float(prev_end)
        if gap >= pause_threshold:
            pauses.append({
                "start": round(float(prev_end), 2),
                "end": round(float(cur_start), 2),
                "duration": round(gap, 2),
            })
    total = sum(p["duration"] for p in pauses)
    return {
        "pause_count": len(pauses),
        "total_pause_time": round(total, 2),
        "pauses": pauses,
    }


def count_fillers(text: str) -> int:
    if not text:
        return 0
    tokens = text.split()
    count = 0
    for token in tokens:
        clean = _normalize_word(token)
        if clean in FILLER_WORDS:
            count += 1
    return count


def count_repetitions(words: List[Dict[str, Any]]) -> int:
    rep = 0
    prev_word: Optional[str] = None
    for item in words:
        current = _normalize_word(str(item.get("word", "")))
        if not current:
            continue
        if prev_word == current:
            rep += 1
        prev_word = current
    return rep


# ============================================================
# 2) 항목별 점수 (총 10점)
#    말 속도 3 + 침묵 3 + 반복어/더듬음 2 + 전달 안정성 2
# ============================================================
def score_speech_rate(wpm: float) -> Dict[str, Any]:
    if 90 <= wpm <= 170:
        return {"score": 3, "level": "적절함",
                "comment": "말 속도가 비교적 안정적입니다."}
    if 70 <= wpm < 90 or 170 < wpm <= 210:
        return {"score": 2, "level": "보통",
                "comment": "말 속도가 다소 느리거나 빠릅니다."}
    return {"score": 1, "level": "개선 필요",
            "comment": "말 속도가 너무 느리거나 빨라 전달력이 떨어질 수 있습니다."}


def score_pauses(pause_count: int, total_pause_time: float, duration_sec: float) -> Dict[str, Any]:
    if duration_sec <= 0:
        return {"score": 1, "level": "분석 부족",
                "comment": "음성 길이가 너무 짧아 침묵 분석이 어렵습니다."}
    ratio = total_pause_time / duration_sec
    if pause_count <= 1 and ratio <= 0.08:
        return {"score": 3, "level": "안정적",
                "comment": "긴 침묵이 거의 없어 답변 흐름이 자연스럽습니다."}
    if pause_count <= 3 and ratio <= 0.18:
        return {"score": 2, "level": "보통",
                "comment": "일부 침묵 구간이 있으나 답변 흐름을 크게 방해하지는 않습니다."}
    return {"score": 1, "level": "개선 필요",
            "comment": "긴 침묵이나 끊김이 많아 답변이 불안정하게 들릴 수 있습니다."}


def score_filler_and_repetition(filler_count: int, repetition_count: int, duration_sec: float) -> Dict[str, Any]:
    minutes = max(duration_sec / 60, 0.1)
    per_min = (filler_count + repetition_count) / minutes
    if per_min <= 3:
        return {"score": 2, "level": "양호",
                "comment": "불필요한 반복어나 더듬는 표현이 적습니다."}
    if per_min <= 7:
        return {"score": 1, "level": "보통",
                "comment": "반복어 또는 불필요 표현이 일부 나타납니다."}
    return {"score": 0, "level": "개선 필요",
            "comment": "반복 표현과 불필요 표현이 많아 답변의 명확성이 떨어질 수 있습니다."}


def score_voice_stability_basic(pause_count: int, filler_count: int, repetition_count: int) -> Dict[str, Any]:
    issues = pause_count + filler_count + repetition_count
    if issues <= 3:
        return {"score": 2, "level": "안정적",
                "comment": "음성 전달이 전반적으로 안정적입니다."}
    if issues <= 8:
        return {"score": 1, "level": "보통",
                "comment": "일부 구간에서 음성 전달이 흔들릴 수 있습니다."}
    return {"score": 0, "level": "개선 필요",
            "comment": "끊김, 반복어, 불필요 표현이 많아 전달 안정성이 낮아 보일 수 있습니다."}


# ============================================================
# 3) 답변 1개에 대한 음성 평가
# ============================================================
def evaluate_voice_nonverbal_from_transcript(
    text: str,
    words: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Whisper 의 text + word-level timestamps 로 음성 비언어 평가 (10점 만점).
    words 가 비어있으면 (텍스트 직접 입력 등) ok=False.
    """
    if not words:
        return {
            "ok": False,
            "reason": "단어별 timestamp 데이터 없음 (텍스트 직접 입력이거나 STT 폴백)",
            "voice_nonverbal_total": 0,
            "max_score": 10,
            "detail_scores": {},
            "metrics": {"text": text, "word_count": 0, "duration_sec": 0},
        }

    rate = calculate_speech_rate(words)
    pause = analyze_pauses(words)
    fillers = count_fillers(text or "")
    reps = count_repetitions(words)

    sr = score_speech_rate(rate["words_per_minute"])
    sp = score_pauses(pause["pause_count"], pause["total_pause_time"], rate["duration_sec"])
    sf = score_filler_and_repetition(fillers, reps, rate["duration_sec"])
    st = score_voice_stability_basic(pause["pause_count"], fillers, reps)

    total = sr["score"] + sp["score"] + sf["score"] + st["score"]
    return {
        "ok": True,
        "voice_nonverbal_total": total,
        "max_score": 10,
        "detail_scores": {
            "speech_rate":       {"max_score": 3, **sr},
            "pause":             {"max_score": 3, **sp},
            "filler_repetition": {"max_score": 2, **sf},
            "voice_stability":   {"max_score": 2, **st},
        },
        "metrics": {
            "text": text,
            "duration_sec":     rate["duration_sec"],
            "word_count":       rate["word_count"],
            "words_per_minute": rate["words_per_minute"],
            "pause_count":      pause["pause_count"],
            "total_pause_time": pause["total_pause_time"],
            "filler_count":     fillers,
            "repetition_count": reps,
            "pauses":           pause["pauses"],
        },
    }


# ============================================================
# 4) 세션 단위 집계 — 답변별 평가를 평균 내어 세션 점수 산출
# ============================================================
def aggregate_voice_evals(per_q: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Q 별 음성 평가 리스트 → 세션 전체 음성 점수 (10점 만점).
    ok=False 인 항목(텍스트 답변 등)은 평균 계산에서 제외.
    유효 답변이 하나도 없으면 ok=False 반환 (점수 0, 100점 만점 표시 불가).
    """
    valid = [e for e in per_q if e and e.get("ok")]
    if not valid:
        return {
            "ok": False,
            "reason": "음성으로 답변된 문항이 없어 음성 평가가 불가합니다 (모두 텍스트 답변).",
            "voice_nonverbal_total": 0,
            "max_score": 10,
            "per_question_count": 0,
        }
    avg = sum(e["voice_nonverbal_total"] for e in valid) / len(valid)
    avg_metrics_keys = ("duration_sec", "words_per_minute", "pause_count",
                        "filler_count", "repetition_count")
    avg_metrics: Dict[str, float] = {}
    for k in avg_metrics_keys:
        vals = [e["metrics"].get(k, 0) or 0 for e in valid]
        avg_metrics[k] = round(sum(vals) / len(vals), 2) if vals else 0
    return {
        "ok": True,
        "voice_nonverbal_total": round(avg, 2),
        "max_score": 10,
        "per_question_count": len(valid),
        "average_metrics": avg_metrics,
    }
