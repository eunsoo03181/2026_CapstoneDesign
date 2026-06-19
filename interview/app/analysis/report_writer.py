"""
면접 결과를 사람이 읽기 좋은 txt + 원본 json 으로 저장.

사용 예:
    from app.analysis.report_writer import save_session_report
    out_dir = save_session_report(result, resume_text)
    print("저장됨:", out_dir)
"""

import os
import json
import shutil
import datetime
from typing import Dict, Optional


def save_session_report(
    result: Dict,
    resume_text: str,
    base_dir: str = "sessions",
    move_wav_files: bool = True,
) -> str:
    """
    면접 결과를 sessions/session_{timestamp}/ 아래에 저장.

    생성물:
      - report.txt   : 사람이 읽는 형태의 종합 리포트
      - data.json    : 원본 데이터 (다시 분석/재처리 가능)
      - answer_*.wav : 질문별 음성 (move_wav_files=True 면 이동)

    반환: 저장된 폴더 경로.
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(base_dir, f"session_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    # 1) JSON 원본
    json_path = os.path.join(out_dir, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"timestamp": ts, "resume": resume_text, **result},
            f, ensure_ascii=False, indent=2,
        )

    # 2) 사람용 txt
    txt_path = os.path.join(out_dir, "report.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        _write_text_report(f, result, resume_text, ts)

    # 3) wav 파일 이동 (이미 CWD 에 만들어진 answer_1.wav, ... 들)
    if move_wav_files:
        n = len(result.get("qa_pairs", []))
        for i in range(1, n + 1):
            src = f"answer_{i}.wav"
            if os.path.exists(src):
                shutil.move(src, os.path.join(out_dir, src))

    return out_dir


# ---------- 내부 헬퍼 ----------

def _hr(ch: str = "=", n: int = 70) -> str:
    return ch * n


def _write_text_report(f, result: Dict, resume_text: str, ts: str):
    pretty_ts = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"

    f.write(_hr() + "\n")
    f.write("                    모의 면접 리포트\n")
    f.write(_hr() + "\n")
    f.write(f"일시: {pretty_ts}\n\n")

    f.write("[이력서]\n")
    f.write(resume_text.strip() + "\n\n")
    f.write(_hr() + "\n\n")

    qa_pairs = result.get("qa_pairs", [])
    eval_items = (result.get("content_evaluation") or {}).get("items", [])

    # 질문/답변/평가 한 묶음씩
    for i, (q, a) in enumerate(qa_pairs, start=1):
        ev = eval_items[i - 1] if i - 1 < len(eval_items) else None

        # 질문은 dict 또는 string 둘 다 호환
        if isinstance(q, dict):
            q_text = q.get("question", "")
            q_intent = q.get("intent", "")
            q_eps = q.get("evaluation_points") or []
        else:
            q_text = str(q)
            q_intent = ""
            q_eps = []

        f.write(f"[질문 {i}] {q_text}\n")
        if q_intent:
            f.write(f"  · 의도: {q_intent}\n")
        if q_eps:
            f.write(f"  · 평가 포인트:\n")
            for p in q_eps:
                f.write(f"      - {p}\n")
        f.write("\n[답변]\n")
        f.write((a or "(빈 답변)").strip() + "\n\n")

        if ev:
            f.write(f"[평가] {ev.get('content_score', 0)} / 80\n")
            f.write(f"  공통 기준 ({ev.get('common_subtotal', 0)} / 50)\n")
            cs = ev.get("common_scores", {}) or {}
            f.write(f"    - 질문 의도 파악:      {cs.get('question_understanding', 0):>2} /  9\n")
            f.write(f"    - 답변 구조성:         {cs.get('answer_structure', 0):>2} /  9\n")
            f.write(f"    - 이력서/직무 관련성:  {cs.get('resume_job_relevance', 0):>2} / 13\n")
            f.write(f"    - 경험의 구체성:       {cs.get('specificity', 0):>2} /  9\n")
            f.write(f"    - 논리성과 설득력:     {cs.get('logic', 0):>2} /  6\n")
            f.write(f"    - 표현의 간결성:       {cs.get('conciseness', 0):>2} /  4\n")

            f.write(f"  맞춤 기준 ({ev.get('custom_subtotal', 0)} / 30)\n")
            for cp in ev.get("custom_scores") or []:
                f.write(f"    - {cp.get('point', '')}: {cp.get('score', 0)} / 5\n")
            f.write("\n")

            strengths = ev.get("strengths") or []
            if strengths:
                f.write("강점:\n")
                for s in strengths:
                    f.write(f"  - {s}\n")
                f.write("\n")

            improvements = ev.get("improvements") or []
            if improvements:
                f.write("개선점:\n")
                for s in improvements:
                    f.write(f"  - {s}\n")
                f.write("\n")

            fb = (ev.get("content_feedback") or "").strip()
            if fb:
                f.write(f"코멘트: {fb}\n\n")

            sample = (ev.get("sample_answer") or "").strip()
            if sample:
                f.write("[모범 답변 예시]\n")
                f.write(sample + "\n\n")

        f.write(_hr("-") + "\n\n")

    # 종합 점수
    final = result.get("final")
    if final:
        f.write(_hr() + "\n")
        f.write("                    종합 점수\n")
        f.write(_hr() + "\n")
        f.write(f"최종 점수    : {final['final_score_100']:>6.2f} / 100\n")
        f.write(f"  답변(내용): {final['content_score_80']:>6.2f} /  80   (질문 평균)\n")
        nv_tag = "" if final.get("nonverbal_available") else " (미수집)"
        f.write(f"  비언어     : {final['nonverbal_score_20']:>6.2f} /  20{nv_tag}\n\n")

        c_summary = final.get("content_summary") or {}
        if c_summary:
            f.write(
                f"답변 합계    : {c_summary.get('total_score', 0)} / "
                f"{c_summary.get('max_total', 0)}  ({c_summary.get('percentage', 0)}%)\n"
            )
            f.write(
                f"  공통 평균  : {c_summary.get('common_average', 0)} / 50\n"
                f"  맞춤 평균  : {c_summary.get('custom_average', 0)} / 30\n"
            )

        nv_summary = final.get("nonverbal_summary") or {}
        if nv_summary.get("ok"):
            s = nv_summary["scores_20"]
            m = nv_summary["metrics"]
            # 3축 통합 스키마. 옛 세션은 focus/blink 분리 — 통합값으로 폴백.
            smile = s.get("smile") or {}
            posture = s.get("posture") or {}
            gaze = s.get("gaze")
            if not gaze:
                # 옛 4축 데이터 (focus 6 + blink 4) → 합산
                focus = s.get("focus") or s.get("_focus_component") or {}
                blink = s.get("blink") or s.get("_blink_component") or {}
                gaze_score = (focus.get("score") or 0) + (blink.get("score") or 0)
                gaze_label = " · ".join(
                    [x for x in (focus.get("label"), blink.get("label")) if x]
                )
                gaze = {"score": gaze_score, "label": gaze_label}
            f.write("\n[비언어 세부 — 3축 통합]\n")
            f.write(f"  - 표정 안정성 : {smile.get('score', 0):>4.2f} / 6   [{smile.get('label', '')}]\n")
            f.write(f"  - 시선 안정성 : {gaze.get('score', 0):>4.2f} / 10  [{gaze.get('label', '')}]\n")
            f.write(f"  - 자세 안정성 : {posture.get('score', 0):>4.2f} / 4   [{posture.get('label', '')}]\n")
            f.write("\n[비언어 원시 지표]\n")
            f.write(f"  미소 비율            : {m.get('smile_ratio', 0)} %\n")
            f.write(f"  시선 정면 응시 비율  : {m.get('focus_ratio', 0)} %\n")
            f.write(f"  분당 깜빡임          : {m.get('blink_per_minute', 0)} 회/분\n")
            f.write(f"  평균 자세 움직임     : {m.get('avg_movement_px', 0)} px/frame\n")
        f.write(_hr() + "\n")
