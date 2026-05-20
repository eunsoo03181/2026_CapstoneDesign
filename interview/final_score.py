"""
최종 점수 합산 — 답변 내용(80) + 시각 비언어(10) + 음성 비언어(10) = 100.

이전엔 답변 80 + 시각 비언어 20 = 100 구조였으나,
음성 비언어 평가(말 속도/침묵/반복어/안정성)를 분리 도입하면서
시각 비언어 점수를 10점으로 축소하고 그 자리에 음성 10점을 채웠습니다.

내부 시각 점수는 여전히 score_20 (0~20) 으로 계산되며,
이 모듈에서 0.5배 리스케일로 visual_10 산출 — 옛 세션·DB 와 호환 유지.

- compute_final_score(content_summary, nonverbal_metrics, voice_eval=None)
    content_summary    : answer_evaluator.evaluate_session() 결과의 'summary'
                         (average_score 필드, 0~80)
    nonverbal_metrics  : nonverbal_analyzer / face_analyzer.compute_metrics() 결과
                         (score_20 필드, 0~20)
    voice_eval         : voice_nonverbal_eval.aggregate_voice_evals() 결과 (선택)
                         (voice_nonverbal_total 필드, 0~10)
"""

from typing import Dict, Optional


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(float(v or 0), hi))


def compute_final_score(
    content_summary: Optional[Dict],
    nonverbal_metrics: Optional[Dict],
    voice_eval: Optional[Dict] = None,
) -> Dict:
    """
    100점 환산: 언어 80 + 시각 비언어 10 + 음성 비언어 10.
    각 점수 누락 시 0점, 100점 만점에서 그만큼 빠짐 — UI 가 미수집 안내.
    """
    content_80 = _clamp(
        (content_summary or {}).get("average_score", 0.0) if content_summary else 0.0,
        0, 80,
    )

    visual_available = bool(nonverbal_metrics and nonverbal_metrics.get("ok"))
    visual_score_20 = float(nonverbal_metrics.get("score_20", 0.0)) if visual_available else 0.0
    visual_10 = _clamp(visual_score_20 / 2.0, 0, 10)

    voice_available = bool(voice_eval and voice_eval.get("ok"))
    voice_10 = _clamp(
        (voice_eval or {}).get("voice_nonverbal_total", 0) if voice_available else 0,
        0, 10,
    )

    # 비언어 합산 (옛 UI 호환: nonverbal_score_20 필드 유지 — 시각 10 + 음성 10 = 20)
    nonverbal_20 = visual_10 + voice_10

    final_100 = content_80 + nonverbal_20

    return {
        "final_score_100":    round(final_100, 2),
        "content_score_80":   round(content_80, 2),
        # ↓ 호환 유지: 기존 컬럼/UI 가 nonverbal_score_20 (0~20) 으로 표시 — 시각+음성 합산
        "nonverbal_score_20": round(nonverbal_20, 2),
        # ↓ 신규 분해 — UI 가 따로 표시
        "visual_score_10":    round(visual_10, 2),
        "voice_score_10":     round(voice_10, 2),
        "visual_available":   visual_available,
        "voice_available":    voice_available,
        # 옛 호환 — 카메라 OFF 면 nonverbal_available=False, content 만 80점 만점 표시
        "nonverbal_available": (visual_available or voice_available),
        "content_summary":    content_summary,
        "nonverbal_summary":  nonverbal_metrics,
        "voice_summary":      voice_eval,
    }


def render_final_report(final: Dict) -> str:
    """간단한 텍스트 리포트(콘솔 출력용)."""
    nv = final.get("nonverbal_summary") or {}
    voice = final.get("voice_summary") or {}
    c_summary = final.get("content_summary") or {}
    lines = [
        "=" * 64,
        "                [ 최종 종합 점수 (100점 만점) ]",
        "=" * 64,
        f"  최종 점수       : {final['final_score_100']:>6.2f} / 100",
        f"   ├─ 답변(내용) : {final['content_score_80']:>6.2f} /  80",
        f"   ├─ 시각 비언어: {final.get('visual_score_10', 0):>6.2f} /  10"
        + ("" if final.get("visual_available") else "  (미수집)"),
        f"   └─ 음성 비언어: {final.get('voice_score_10', 0):>6.2f} /  10"
        + ("" if final.get("voice_available") else "  (미수집)"),
        "-" * 64,
    ]
    if c_summary:
        lines.append(
            f"  답변 합계       : {c_summary.get('total_score', 0)} / "
            f"{c_summary.get('max_total', 0)}  ({c_summary.get('percentage', 0)}%)"
        )
    if nv.get("ok"):
        s = nv["scores_20"]
        m = nv["metrics"]
        smile = s.get("smile") or {}
        posture = s.get("posture") or {}
        gaze = s.get("gaze")
        if not gaze:
            # 옛 4축 데이터(focus 6 + blink 4) → 통합값으로 폴백
            focus = s.get("focus") or s.get("_focus_component") or {}
            blink = s.get("blink") or s.get("_blink_component") or {}
            gaze = {
                "score": (focus.get("score") or 0) + (blink.get("score") or 0),
                "max": 10,
                "label": " · ".join(
                    [x for x in (focus.get("label"), blink.get("label")) if x]
                ),
            }
        lines += [
            f"  시각 세부       : 표정 {smile.get('score', 0)}/{smile.get('max', 6):.0f} "
            f"[{smile.get('label', '')}] · 시선 {gaze.get('score', 0)}/{gaze.get('max', 10):.0f} "
            f"[{gaze.get('label', '')}]",
            f"                    자세 {posture.get('score', 0)}/{posture.get('max', 4):.0f} "
            f"[{posture.get('label', '')}]",
            f"  시각 지표       : 미소 {m.get('smile_ratio', 0)}% / 시선 {m.get('focus_ratio', 0)}% / "
            f"분당깜빡임 {m.get('blink_per_minute', 0)} / 평균움직임 {m.get('avg_movement_px', 0)}px/f",
        ]
    if voice.get("ok"):
        d = voice.get("average_metrics") or {}
        lines += [
            f"  음성 세부       : 평균 WPM {d.get('words_per_minute', 0):.1f} · "
            f"평균 침묵 {d.get('pause_count', 0):.1f}회 · "
            f"평균 filler+rep {(d.get('filler_count', 0) + d.get('repetition_count', 0)):.1f}회",
            f"                    (유효 답변 {voice.get('per_question_count', 0)}개 기준 평균)",
        ]
    lines.append("=" * 64)
    return "\n".join(lines)
