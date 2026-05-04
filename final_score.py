"""
답변 내용(80점) + 비언어(20점) = 최종 100점 종합 점수.

- compute_final_score(content_summary, nonverbal_metrics)
    content_summary    : answer_evaluator.evaluate_session() 결과의 'summary'
                         (average_score 필드, 0~80)
    nonverbal_metrics  : nonverbal_analyzer.compute_metrics() 결과
                         (score_20 필드, 0~20)
"""

from typing import Dict, Optional


def compute_final_score(
    content_summary: Dict,
    nonverbal_metrics: Optional[Dict],
) -> Dict:
    content_80 = float(content_summary.get("average_score", 0.0)) if content_summary else 0.0

    if nonverbal_metrics and nonverbal_metrics.get("ok", False):
        nonverbal_20 = float(nonverbal_metrics.get("score_20", 0.0))
        nonverbal_available = True
    else:
        nonverbal_20 = 0.0
        nonverbal_available = False

    final_100 = content_80 + nonverbal_20

    return {
        "final_score_100": round(final_100, 2),
        "content_score_80": round(content_80, 2),
        "nonverbal_score_20": round(nonverbal_20, 2),
        "nonverbal_available": nonverbal_available,
        "content_summary": content_summary,
        "nonverbal_summary": nonverbal_metrics,
    }


def render_final_report(final: Dict) -> str:
    """간단한 텍스트 리포트(콘솔 출력용)."""
    nv = final.get("nonverbal_summary") or {}
    c_summary = final.get("content_summary") or {}
    lines = [
        "=" * 64,
        "                [ 최종 종합 점수 (100점 만점) ]",
        "=" * 64,
        f"  최종 점수       : {final['final_score_100']:>6.2f} / 100",
        f"   ├─ 답변(내용) : {final['content_score_80']:>6.2f} /  80   (질문 평균)",
        f"   └─ 비언어     : {final['nonverbal_score_20']:>6.2f} /  20"
        + ("" if final.get("nonverbal_available") else "  (미수집)"),
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
        lines += [
            f"  비언어 세부     : 표정 {s['smile']['score']}/{s['smile']['max']:.0f} "
            f"[{s['smile']['label']}] · 시선 {s['focus']['score']}/{s['focus']['max']:.0f} "
            f"[{s['focus']['label']}]",
            f"                    깜빡임 {s['blink']['score']}/{s['blink']['max']:.0f} "
            f"[{s['blink']['label']}] · 자세 {s['posture']['score']}/{s['posture']['max']:.0f} "
            f"[{s['posture']['label']}]",
            f"  비언어 지표     : 미소 {m['smile_ratio']}% / 시선 {m['focus_ratio']}% / "
            f"분당깜빡임 {m['blink_per_minute']} / 평균움직임 {m['avg_movement_px']}px/f",
        ]
    lines.append("=" * 64)
    return "\n".join(lines)
