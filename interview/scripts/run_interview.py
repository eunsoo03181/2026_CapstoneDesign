"""
통합 데모: 이력서 → 질문 생성 → (카메라 비언어 분석 동시 실행)
        → 음성 답변 수집 → 텍스트 변환 → 답변 평가(80) + 비언어(20) = 100점 종합.

[현재 활성 STT] OpenAI Whisper
  - 환경변수: OPENAI_API_KEY 만 있으면 동작

[필수 환경변수]
  OPENAI_API_KEY       : 질문 생성 + 답변 채점 + Whisper STT 모두에 사용

[보존(주석) — CLOVA 사용 시]
  NCP_CLIENT_ID / NCP_CLIENT_SECRET    : CLOVA CSR (~60초)
  CLOVA_INVOKE_URL / CLOVA_SECRET_KEY  : CLOVA Speech 장문 인식
"""

import json
import time

from app.questions.question_generator import build_interview_questions
from app.services.speech_to_text import capture_user_answer
from app.scoring.answer_evaluator import evaluate_session
from app.scoring.final_score import compute_final_score, render_final_report
from app.analysis.report_writer import save_session_report
# nonverbal_analyzer 는 with_camera=True 일 때만 import (cv2/mediapipe 의존성 회피)


CALIBRATION_WAIT_SEC = 4.0  # 카메라 캘리브레이션(3초) + 약간의 여유


def run(
    resume_text: str,
    answer_seconds: float = 180.0,    # 한 질문당 최대 녹음 시간 (Enter로 조기 종료 가능)
    evaluate: bool = True,
    with_camera: bool = True,
    show_camera_window: bool = False,
    stt_backend: str = "whisper",      # 활성: "whisper". 보존(주석): "clova_csr" | "clova_long"
    save_report: bool = True,          # 결과를 sessions/ 폴더에 저장
    # long_form: bool = False,         # CLOVA 장문 인식 옵션 (현재 비활성)
):
    # 1) 면접 질문 생성 (테스트 단계: 맞춤 2 + 공통 1 = 3개. 정식: 5+2)
    interview_questions = build_interview_questions(
        resume_text, n_personalized=2, n_common=1
    )

    # 2) 카메라 비언어 분석 시작 (백그라운드)
    bg = None
    if with_camera:
        from app.nonverbal.nonverbal_analyzer import BackgroundAnalyzer  # cv2/mediapipe 필요
        bg = BackgroundAnalyzer(show_window=show_camera_window)
        bg.start()
        print("카메라 캘리브레이션 중... 약 3초 동안 무표정으로 카메라를 응시해주세요.")
        time.sleep(CALIBRATION_WAIT_SEC)
        print("캘리브레이션 종료. 면접을 시작합니다.\n")

    # 3) 질문별 음성 답변 → 텍스트
    user_answers = []  # [(질문 dict, 답변텍스트), ...]
    for idx, q in enumerate(interview_questions, 1):
        q_text = q["question"] if isinstance(q, dict) else str(q)
        print(f"\n[Q{idx}] {q_text}")
        input("  ▶ 준비되면 Enter를 누르세요. ")
        wav_path = f"answer_{idx}.wav"
        text = capture_user_answer(
            save_path=wav_path,
            seconds=answer_seconds,
            backend=stt_backend,
            interactive=True,
        )
        print(f"  변환된 답변: {text}")
        user_answers.append((q, text))   # q 는 dict 그대로 보존

    # 4) 비언어 분석 종료 → 지표 수집
    nonverbal_metrics = bg.stop() if bg else None

    # 5) 답변 내용 평가 (80점)
    evaluation = None
    if evaluate and user_answers:
        evaluation = evaluate_session(user_answers, resume_summary=resume_text)

    # 6) 최종 종합 점수 (80 + 20 = 100)
    final = None
    if evaluation:
        final = compute_final_score(evaluation["summary"], nonverbal_metrics)

    result = {
        "questions": interview_questions,
        "qa_pairs": user_answers,
        "content_evaluation": evaluation,
        "nonverbal_metrics": nonverbal_metrics,
        "final": final,
    }

    # 7) 결과 저장 (txt + json + wav)
    if save_report:
        out_dir = save_session_report(result, resume_text=resume_text)
        result["saved_to"] = out_dir
        print(f"\n결과 저장됨: {out_dir}/report.txt")

    return result


if __name__ == "__main__":
    import sys

    # 인자로 파일 경로(.txt/.docx/.pdf/.hwp/.hwpx) 가 오면 그 파일을 읽음.
    # 없으면 하드코딩 샘플로 진행.
    if len(sys.argv) > 1:
        from app.services.resume_loader import load_resume
        resume_path = sys.argv[1]
        sample_resume = load_resume(resume_path)
        print(f"이력서 로드: {resume_path} ({len(sample_resume)}자)\n")
    else:
        sample_resume = """
        이름: 홍길동
        학력: OO대학교 전자공학과 졸업
        경력: ABC 스타트업 백엔드 인턴 6개월, Python/Django
        프로젝트: 졸음 감지 시스템 (OpenCV, MediaPipe)
        기술 스택: Python, Django, PostgreSQL, Docker
        """
        print("이력서: 하드코딩 샘플 사용 (파일 경로를 인자로 주면 그 파일을 읽음)\n")

    # 기본값: 한 질문당 최대 180초, Enter로 즉시 종료.
    # 카메라는 단독 흐름 검증 후에 with_camera=True 로 켜세요.
    result = run(
        sample_resume,
        answer_seconds=180.0,
        with_camera=False,
        stt_backend="whisper",   # CLOVA 재활성화 시 "clova_csr" 또는 "clova_long"
    )

    print("\n=== 질문/답변 ===")
    for q, a in result["qa_pairs"]:
        q_text = q["question"] if isinstance(q, dict) else str(q)
        print(f"Q: {q_text}\nA: {a}\n")

    if result["content_evaluation"]:
        print("=== 답변 평가 (질문별 80점 만점) ===")
        print(json.dumps(result["content_evaluation"], ensure_ascii=False, indent=2))

    if result["final"]:
        print(render_final_report(result["final"]))
