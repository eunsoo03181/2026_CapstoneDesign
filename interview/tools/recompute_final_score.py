"""
일회성 — leniency 가 적용된 세션의 최종 점수를 원본(leniency=1.0) 기준으로 재계산.

사용:
    cd /Users/eunsoo/Documents/Claude/Projects/Capstone/interview
    source venv.nosync/bin/activate   # 또는 venv/bin/activate
    python tools/recompute_final_score.py I0pnaCCwew
    # 여러 개:
    python tools/recompute_final_score.py I0pnaCCwew XyZAbc1234

옵션:
    --dry-run   : DB 저장 없이 새 값만 출력 (기본 권장 — 먼저 확인)
    --leniency 0.7   : 1.0 외 다른 값으로 재계산하고 싶을 때
"""

import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 import path 에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env 로드 — DATABASE_URL 이 거기 있으니
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from db import SessionLocal
from db.models import InterviewSession
from app.scoring.final_score import compute_final_score


def recompute(public_code: str, *, leniency: float = 1.0, dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        s = (
            db.query(InterviewSession)
              .filter(InterviewSession.public_code == public_code)
              .first()
        )
        if not s:
            print(f"❌ public_code={public_code} — 세션을 찾을 수 없습니다.")
            return

        # 1) content — evaluations.content_score 의 평균 (원본, leniency 영향 안 받음)
        content_scores = []
        for q in s.questions:
            if q.answer and q.answer.evaluation:
                content_scores.append(float(q.answer.evaluation.content_score or 0))
        if not content_scores:
            print(f"⚠️ public_code={public_code} — 평가된 답변이 없음. content=0")
            content_avg = 0.0
        else:
            content_avg = sum(content_scores) / len(content_scores)

        # 2) nonverbal — DB 의 NonverbalMetrics 에서 raw 점수
        nv = s.nonverbal_metrics
        raw = (nv.raw_metrics_json if nv else None) or {}

        # 시각 metrics — raw['ok'] 또는 score_20 > 0 이면 visual_available
        visual_score_20_raw = float(nv.score_20) if nv else 0.0
        visual_ok = bool(visual_score_20_raw > 0 or (isinstance(raw, dict) and raw.get("ok")))
        nonverbal_metrics = {"ok": True, "score_20": visual_score_20_raw} if visual_ok else None

        # 음성 metrics — raw['voice_eval'] 에 저장되어 있음
        voice_eval = raw.get("voice_eval") if isinstance(raw, dict) else None

        # 3) 재계산
        content_summary = {"average_score": content_avg}
        new_final = compute_final_score(
            content_summary, nonverbal_metrics, voice_eval,
            leniency_factor=leniency,
        )

        # 4) 출력 — 비교 표
        print(f"\n=== {public_code} 재계산 결과 (leniency={leniency}) ===")
        print(f"  답변 평균 (raw evaluations.content_score 평균): {content_avg:.2f}")
        print()
        print(f"  {'':12} {'이전 (DB)':>10}   {'새 값':>10}   {'변화':>8}")
        print(f"  {'-'*12} {'-'*10}   {'-'*10}   {'-'*8}")
        rows = [
            ("final_100",      float(s.final_score_100 or 0),     new_final["final_score_100"]),
            ("content_80",     float(s.content_score_80 or 0),    new_final["content_score_80"]),
            ("nonverbal_20",   float(s.nonverbal_score_20 or 0),  new_final["nonverbal_score_20"]),
        ]
        for label, before, after in rows:
            delta = after - before
            sign = "+" if delta >= 0 else ""
            print(f"  {label:12} {before:>10.2f}   {after:>10.2f}   {sign}{delta:>7.2f}")

        if dry_run:
            print(f"\n  ⚠️  --dry-run 모드 — DB 저장 안 함. 결과 확정하려면 --dry-run 빼고 다시 실행.\n")
            return

        # 5) DB 업데이트
        s.final_score_100    = new_final["final_score_100"]
        s.content_score_80   = new_final["content_score_80"]
        s.nonverbal_score_20 = new_final["nonverbal_score_20"]
        db.commit()
        print(f"\n  ✅ DB 업데이트 완료.\n")
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="세션 final 점수 재계산 (leniency 복원용)")
    p.add_argument("codes", nargs="+", help="public_code 들 (공백 구분)")
    p.add_argument("--leniency", type=float, default=1.0,
                   help="재계산에 쓸 leniency (기본 1.0 = 원본 복원)")
    p.add_argument("--dry-run", action="store_true",
                   help="DB 저장 없이 결과만 출력")
    args = p.parse_args()

    for code in args.codes:
        recompute(code, leniency=args.leniency, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
