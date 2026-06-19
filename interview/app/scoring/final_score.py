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
    *,
    leniency_factor: float = 1.0,    # 디버그 — 점수 보정 계수 (0.1 ~ 3.0). admin 만 사용
                                      # 1.0=기본, <1.0=깎기, >1.0=후함. 언어·시각·음성 모든 컴포넌트에 동일 적용
) -> Dict:
    """
    100점 환산. 기본 점수 구조: 언어 80 + 시각 비언어 10 + 음성 비언어 10.

    카메라 OFF / 텍스트 답변 등으로 시각·음성 평가가 빠지면 그 항목의 만점이
    합계에서 제외되고, 남은 항목 합계를 100점으로 비례 환산.

    예시
      - 카메라 OFF + 텍스트 답변   → 만점 80,  ×1.25  (= 100 / 80)
      - 카메라 OFF + 음성 답변     → 만점 90,  ×1.111 (= 100 / 90)
      - 카메라 ON  + 텍스트 답변   → 만점 90,  ×1.111
      - 카메라 ON  + 음성 답변     → 만점 100, ×1.0   (환산 없음)
    """
    # 원시 점수 산출 (보정 적용 전)
    content_80_raw = _clamp(
        (content_summary or {}).get("average_score", 0.0) if content_summary else 0.0,
        0, 80,
    )

    visual_available = bool(nonverbal_metrics and nonverbal_metrics.get("ok"))
    visual_score_20_raw = float(nonverbal_metrics.get("score_20", 0.0)) if visual_available else 0.0
    # 내부 만점은 항상 20 (face_analyzer.compute_metrics 반환). UI 표시용 컴포넌트 max 는 아래에서 결정.

    voice_available = bool(voice_eval and voice_eval.get("ok"))
    voice_total_raw = float((voice_eval or {}).get("voice_nonverbal_total", 0)) if voice_available else 0.0
    # voice_total 의 내부 만점은 항상 10 (voice_nonverbal_eval). 한 축만 살아있으면 ×2 흡수.

    # ── 점수 체계 — 한 축만 살아있으면 그 축이 만점 20 흡수 ─────────────────
    #   둘 다 있음:  시각 10 + 음성 10  = 20 (비언어)
    #   voice 없음:  시각 20            = 20 (visual_score_20 그대로)
    #   visual 없음: 음성 20            = 20 (voice_total × 2)
    #   둘 다 없음:  비언어 0           → 언어 80 만 → 환산 ×1.25
    if visual_available and voice_available:
        visual_component_raw = _clamp(visual_score_20_raw / 2.0, 0, 10)
        voice_component_raw  = _clamp(voice_total_raw, 0, 10)
        visual_max, voice_max = 10.0, 10.0
    elif visual_available and not voice_available:
        visual_component_raw = _clamp(visual_score_20_raw, 0, 20)     # 20점 그대로 흡수
        voice_component_raw  = 0.0
        visual_max, voice_max = 20.0, 0.0
    elif voice_available and not visual_available:
        visual_component_raw = 0.0
        voice_component_raw  = _clamp(voice_total_raw * 2.0, 0, 20)   # 10 → 20 흡수
        visual_max, voice_max = 0.0, 20.0
    else:
        visual_component_raw = 0.0
        voice_component_raw  = 0.0
        visual_max, voice_max = 0.0, 0.0

    # ── 디버그 leniency — 컴포넌트별 만점으로 clamp 해 각 항목 막대가 자연스럽게 보이도록.
    leniency = _clamp(leniency_factor, 0.1, 3.0)
    content_80   = _clamp(content_80_raw       * leniency, 0, 80)
    visual_score = _clamp(visual_component_raw * leniency, 0, visual_max if visual_max > 0 else 0)
    voice_score  = _clamp(voice_component_raw  * leniency, 0, voice_max  if voice_max  > 0 else 0)

    # 1) raw 점수 — 평가된 항목만 합산
    raw_total = content_80 + visual_score + voice_score
    # 2) raw 만점 — 언어 80 + 비언어 만점 (10+10 또는 20+0 또는 0+20 또는 0+0)
    raw_max_total = 80.0 + visual_max + voice_max
    # 3) 100점 비례 환산 — 비언어 자체가 빠진 경우(=둘 다 미평가)만 환산 작동
    if raw_max_total > 0:
        scale_factor = 100.0 / raw_max_total
        final_100 = raw_total * scale_factor
    else:
        scale_factor = 1.0
        final_100 = 0.0
    # 안전 클램프 (수치 오차 방지)
    final_100 = _clamp(final_100, 0, 100)

    # 보정 전 final (디버그 응답용 — leniency=1.0 이었을 때의 가상 점수)
    raw_total_pre = content_80_raw + visual_component_raw + voice_component_raw
    final_100_pre_leniency = round(raw_total_pre * scale_factor, 2) if raw_max_total > 0 else 0.0

    # 비언어 합산 (옛 UI 호환: nonverbal_score_20 필드 — 시각 + 음성, 항상 0~20 범위)
    nonverbal_20 = visual_score + voice_score

    return {
        "final_score_100":    round(final_100, 2),       # ← 항상 100점 만점 환산값
        "content_score_80":   round(content_80, 2),
        # ↓ 호환 유지: 기존 컬럼/UI 가 nonverbal_score_20 (0~20) 으로 표시 — 시각+음성 합산
        "nonverbal_score_20": round(nonverbal_20, 2),
        # ↓ 분해 — UI 가 항목별 막대로 표시 (max 는 동적: 10/10, 20/0, 0/20, 0/0)
        "visual_score_10":    round(visual_score, 2),   # 이름은 _10 호환 유지, 실제 max 는 visual_max
        "voice_score_10":     round(voice_score, 2),
        "visual_max":         round(visual_max, 2),
        "voice_max":          round(voice_max, 2),
        "visual_available":   visual_available,
        "voice_available":    voice_available,
        # 옛 호환 — 비언어 평가가 하나도 없으면 False
        "nonverbal_available": (visual_available or voice_available),
        # ── 환산 메타 (UI 안내용) ──
        "raw_total":          round(raw_total, 2),
        "raw_max_total":      round(raw_max_total, 2),
        "scale_factor":       round(scale_factor, 4),
        "is_scaled":          (raw_max_total < 100.0),     # True 면 환산이 적용됨
        # ── leniency 메타 (admin 디버그용) ──
        "leniency_factor":    round(leniency, 3),
        "leniency_applied":   (abs(leniency - 1.0) > 1e-6),   # 1.0 미만이든 초과든 적용 상태
        "final_100_pre_leniency": round(final_100_pre_leniency, 2),
        "content_summary":    content_summary,
        "nonverbal_summary":  nonverbal_metrics,
        "voice_summary":      voice_eval,
    }


def render_final_report(final: Dict) -> str:
    """간단한 텍스트 리포트(콘솔 출력용)."""
    nv = final.get("nonverbal_summary") or {}
    voice = final.get("voice_summary") or {}
    c_summary = final.get("content_summary") or {}
    scale_note = ""
    if final.get("is_scaled"):
        scale_note = (
            f"  ※ 환산 적용 : 원본 {final.get('raw_total', 0):.2f} / {final.get('raw_max_total', 0):.0f} "
            f"→ ×{final.get('scale_factor', 1.0):.3f} → 100점 환산"
        )
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
    ]
    if scale_note:
        lines.append(scale_note)
    lines.append("-" * 64)
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
