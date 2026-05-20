"""
비언어 분석 결과 → 자연스러운 한국어 피드백 (조언).

기준은 nonverbal_analyzer.py 의 평가 체계 (3축 통합):
  - 표정 안정성  6점  (smile_ratio 20%↑ 만점)
  - 시선 안정성 10점  (focus 6 + blink 4 통합 — focus_ratio 75%↑ 만점, blink 10~25/min 만점)
  - 자세 안정성  4점  (1.5px/f 이하 만점)

OpenAI 호출은 항상 gpt-4o-mini 로 고정 (얼굴 인식 피드백 전용).
"""

import os
import json
from typing import Dict, Optional

from openai import OpenAI


SYSTEM_PROMPT = """너는 비대면 면접 코치이다. 지원자의 면접 중 비언어 행동 측정 결과를 보고
구체적이고 친절한 한국어 조언을 작성한다.

평가 기준 (총 20점, 3축 통합):
  1. 표정 안정성  6점 — 미소 비율 20% 이상이면 만점
  2. 시선 안정성 10점 — 응시 비율 75% 이상(6점) + 분당 깜빡임 10~25회(4점) 통합
  3. 자세 안정성  4점 — 프레임당 평균 이동 1.5px 이하면 안정

가이드:
- 3가지 항목 각각에 대해 짧고 구체적인 코칭 한 줄을 작성한다.
- 좋은 점은 짧게 인정하고, 부족한 점은 실행 가능한 개선 행동으로 제시한다.
- "표정이 부족합니다" 식의 평가만 하지 말고 "거울 보며 자기소개 3회 연습" 같은 행동 단계를 제시한다.
- 시선 안정성 항목은 응시와 깜빡임을 종합해 한 줄 코칭을 작성한다.
  예: 응시는 양호하나 깜빡임이 많다 → "응시는 안정적이나 깜빡임이 다소 잦으니, 들숨에서 한 박자 시선을 머무는 연습."
- 종합 코멘트는 2~3문장으로 전체 인상을 정리한다.
- 인성/외모/성격은 절대 평가하지 않는다 (오직 측정 가능한 행동만).

출력 JSON 형식 (반드시 이 키만):
{
  "summary": "종합 2~3문장",
  "smile":   "표정 안정성 코칭 한 줄",
  "gaze":    "시선 안정성 코칭 한 줄 (응시 + 깜빡임 통합)",
  "posture": "자세 안정성 코칭 한 줄",
  "next_action": "다음 면접 전에 가장 우선 연습할 한 가지"
}"""


def generate_nonverbal_feedback(
    metrics: Dict,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> Dict:
    """
    metrics: nonverbal_analyzer / face_analyzer.js 의 compute_metrics() 결과 dict.
             score_20 / scores_20 / metrics 키를 포함해야 함.

    반환: {summary, smile, focus, blink, posture, next_action}
    """
    if not metrics or not metrics.get("ok"):
        return {
            "summary": "측정 시간이 부족해 비언어 피드백을 만들지 못했어요. 다음 면접에선 카메라 앞에서 30초 이상 진행해 보세요.",
            "smile": "", "gaze": "", "posture": "",
            "next_action": "",
        }

    s = metrics.get("scores_20", {}) or {}
    m = metrics.get("metrics", {}) or {}

    # gaze 점수 (없으면 focus+blink 합산 — 옛 데이터 호환)
    gaze_block = s.get("gaze") or {}
    if not gaze_block:
        focus_b = s.get("focus") or s.get("_focus_component") or {}
        blink_b = s.get("blink") or s.get("_blink_component") or {}
        gaze_score = (focus_b.get("score") or 0) + (blink_b.get("score") or 0)
        gaze_label = " · ".join([x for x in (focus_b.get("label"), blink_b.get("label")) if x])
        gaze_block = {"score": round(gaze_score, 2), "label": gaze_label}

    # 모델이 보기 쉬운 요약 dict (3축)
    user_payload = {
        "총점": metrics.get("score_20"),
        "지표": {
            "미소 비율 (%)":      m.get("smile_ratio"),
            "정면 응시 비율 (%)": m.get("focus_ratio"),
            "분당 깜빡임":        m.get("blink_per_minute"),
            "평균 자세 이동 (px/f)": m.get("avg_movement_px"),
        },
        "항목별 점수 (3축)": {
            "표정 안정성":  {"점수": s.get("smile",   {}).get("score"), "만점": 6,
                          "라벨": s.get("smile",   {}).get("label")},
            "시선 안정성":  {"점수": gaze_block.get("score"),            "만점": 10,
                          "라벨": gaze_block.get("label"),
                          "분해": {
                              "응시(75% 만점)": (s.get("_focus_component") or s.get("focus") or {}).get("score"),
                              "깜빡임(10~25/min 만점)": (s.get("_blink_component") or s.get("blink") or {}).get("score"),
                          }},
            "자세 안정성":  {"점수": s.get("posture", {}).get("score"), "만점": 4,
                          "라벨": s.get("posture", {}).get("label")},
        },
        "진행 시간(초)": metrics.get("duration_sec"),
    }

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_completion_tokens=600,
    )
    try:
        data = json.loads(resp.choices[0].message.content)
    except Exception:
        data = {}
    # 누락 키 보완 (3축 + summary + next_action)
    for k in ("summary", "smile", "gaze", "posture", "next_action"):
        data.setdefault(k, "")
    return data
