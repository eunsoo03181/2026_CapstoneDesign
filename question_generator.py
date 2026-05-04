"""
이력서 텍스트 기반 면접 질문 생성기.

OpenAI API로 이력서 맞춤 질문을 만들고, 내부 풀에서 공통 질문을 섞어
최종 질문 리스트를 변수에 저장한다.
"""

import os
import json
import random
from typing import List

from openai import OpenAI


# 위치가 고정된 앵커 질문 (자기소개=맨 앞, 마지막 멘트=맨 끝)
INTRO_QUESTION = "간단한 자기소개 부탁드립니다."
CLOSING_QUESTION = "마지막으로 하고 싶은 말씀이 있으신가요?"

COMMON_QUESTIONS_POOL: List[str] = [
    INTRO_QUESTION,
    "본인의 장점과 단점을 한 가지씩 말씀해 주세요.",
    "저희 회사(또는 직무)에 지원하신 동기는 무엇인가요?",
    "최근에 가장 열정을 쏟았던 프로젝트나 경험을 소개해 주세요.",
    "팀 내에서 갈등이 있었던 경험과 해결 과정을 말씀해 주세요.",
    "5년 후 본인의 모습은 어떠한 모습일 것 같나요?",
    CLOSING_QUESTION,
]


SYSTEM_PROMPT = (
    "당신은 한국어 면접관입니다. 지원자의 이력서를 읽고, "
    "지원자의 경험·기술·프로젝트에 근거한 구체적이고 깊이 있는 면접 질문을 만들어 주세요. "
    "질문은 한국어로, 한 문장으로, 정중한 존댓말로 작성합니다. "
    "출력은 반드시 JSON 형식 {\"questions\": [\"질문1\", \"질문2\", ...]} 으로만 답하세요."
)


def generate_personalized_questions(
    resume_text: str,
    n: int = 5,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> List[str]:
    """이력서 텍스트로부터 맞춤형 질문 n개를 생성해 리스트로 반환."""
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    user_prompt = (
        f"다음은 지원자의 이력서입니다.\n\n---\n{resume_text}\n---\n\n"
        f"위 이력서에 근거하여 면접 질문 {n}개를 생성해 주세요."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )

    content = response.choices[0].message.content
    data = json.loads(content)
    questions = data.get("questions", [])
    return [q.strip() for q in questions if isinstance(q, str) and q.strip()]


def pick_common_questions(n: int = 2) -> List[str]:
    """공통 질문 풀에서 무작위로 n개 추출."""
    n = min(n, len(COMMON_QUESTIONS_POOL))
    return random.sample(COMMON_QUESTIONS_POOL, n)


def build_interview_questions(
    resume_text: str,
    n_personalized: int = 5,
    n_common: int = 2,
    shuffle: bool = False,
) -> List[str]:
    """
    최종 면접 질문 리스트를 생성.

    순서 규칙(중요):
      - 공통 질문 풀에서 '자기소개' 가 뽑히면 → 무조건 리스트 맨 앞.
      - 공통 질문 풀에서 '마지막으로 하고 싶은 말...' 이 뽑히면 → 무조건 리스트 맨 끝.
      - 그 외(중간 공통 질문 + 이력서 맞춤 질문)는 사이에 배치.
      - shuffle=True 라도 위 두 앵커 위치는 흔들리지 않는다.
    """
    common = pick_common_questions(n_common)
    personalized = generate_personalized_questions(resume_text, n=n_personalized)

    has_intro = INTRO_QUESTION in common
    has_closing = CLOSING_QUESTION in common
    middle_common = [
        q for q in common if q not in (INTRO_QUESTION, CLOSING_QUESTION)
    ]

    middle = middle_common + personalized
    if shuffle:
        random.shuffle(middle)

    questions: List[str] = []
    if has_intro:
        questions.append(INTRO_QUESTION)
    questions.extend(middle)
    if has_closing:
        questions.append(CLOSING_QUESTION)
    return questions


if __name__ == "__main__":
    sample_resume = """
    이름: 홍길동
    학력: OO대학교 전자공학과 졸업
    경력:
      - ABC 스타트업 백엔드 인턴 (Python/Django, 6개월)
      - 졸업작품: 졸음 감지 시스템 (OpenCV, MediaPipe)
    기술 스택: Python, Django, PostgreSQL, Docker
    """

    interview_questions = build_interview_questions(
        sample_resume, n_personalized=5, n_common=2
    )

    print("=== 생성된 면접 질문 ===")
    for i, q in enumerate(interview_questions, 1):
        print(f"{i}. {q}")
