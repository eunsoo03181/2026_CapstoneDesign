"""
통합 데모: 이력서 → 질문 생성 → (카메라 비언어 분석 동시 실행)
        → 음성 답변 수집 → 텍스트 변환 → 답변 평가(80) + 비언어(20) = 100점 종합.

[필수 환경변수]
  OPENAI_API_KEY       : OpenAI API 키
  NCP_CLIENT_ID        : Naver CLOVA CSR Client ID  (짧은 답변용)
  NCP_CLIENT_SECRET    : Naver CLOVA CSR Client Secret
  (선택) CLOVA_INVOKE_URL, CLOVA_SECRET_KEY  : 장문 인식 사용 시
"""

import json
import time

from question_generator import build_interview_questions
from speech_to_text import capture_user_answer
from answer_evaluator import evaluate_session
from nonverbal_analyzer import BackgroundAnalyzer
from final_score import compute_final_score, render_final_report


CALIBRATION_WAIT_SEC = 4.0  # 카메라 캘리브레이션(3초) + 약간의 여유


def run(
    resume_text: str,
    answer_seconds: float = 30.0,
    long_form: bool = False,
    evaluate: bool = True,
    with_camera: bool = True,
    show_camera_window: bool = False,
):
    # 1) 면접 질문 생성
    interview_questions = build_interview_questions(
        resume_text, n_personalized=5, n_common=2
    )

    # 2) 카메라 비언어 분석 시작 (백그라운드)
    bg = None
    if with_camera:
        bg = BackgroundAnalyzer(show_window=show_camera_window)
        bg.start()
        print("카메라 캘리브레이션 중... 약 3초 동안 무표정으로 카메라를 응시해주세요.")
        time.sleep(CALIBRATION_WAIT_SEC)
        print("캘리브레이션 종료. 면접을 시작합니다.\n")

    # 3) 질문별 음성 답변 → 텍스트
    user_answers = []  # [(질문, 답변텍스트), ...]
    for idx, q in enumerate(interview_questions, 1):
        print(f"\n[Q{idx}] {q}")
        input("  ▶ 준비되면 Enter를 누르세요. ")
        wav_path = f"answer_{idx}.wav"
        text = capture_user_answer(
            save_path=wav_path, seconds=answer_seconds, long_form=long_form
        )
        print(f"  변환된 답변: {text}")
        user_answers.append((q, text))

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

    return {
        "questions": interview_questions,
        "qa_pairs": user_answers,
        "content_evaluation": evaluation,
        "nonverbal_metrics": nonverbal_metrics,
        "final": final,
    }


if __name__ == "__main__":
    sample_resume = """
    이름: 홍길동
    학력: OO대학교 전자공학과 졸업
    경력: ABC 스타트업 백엔드 인턴 6개월, Python/Django
    프로젝트: 졸음 감지 시스템 (OpenCV, MediaPipe)
    기술 스택: Python, Django, PostgreSQL, Docker
    """
    result = run(sample_resume, answer_seconds=30.0, with_camera=True)

    print("\n=== 질문/답변 ===")
    for q, a in result["qa_pairs"]:
        print(f"Q: {q}\nA: {a}\n")

    if result["content_evaluation"]:
        print("=== 답변 평가 (질문별 80점 만점) ===")
        print(json.dumps(result["content_evaluation"], ensure_ascii=False, indent=2))

    if result["final"]:
        print(render_final_report(result["final"]))
