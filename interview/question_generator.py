"""
이력서 텍스트 기반 면접 질문 생성기.

각 질문은 다음 구조의 dict 로 만들어진다:
  {
    "question_id":      "C_INTRO" | "P01" | ...,
    "question":         "...",
    "intent":           "이 질문으로 무엇을 평가하려는지",
    "evaluation_points": ["...", "...", "..."]   # 답변에서 보고 싶은 포인트
  }

공통 질문은 미리 정의된 의도/평가 포인트를 사용하고,
맞춤 질문은 OpenAI 가 질문 + 의도 + 평가 포인트를 한 번에 생성한다.
"""

import os
import json
import random
import asyncio
from typing import List, Dict, Optional

from openai import OpenAI, AsyncOpenAI


# 자소서 길이 임계값 (문자 기준 — 한국어는 1자 ≈ 0.7~1 토큰)
RESUME_SUMMARIZE_THRESHOLD = 4000
RESUME_HARD_CAP = 15000


# ============================================================
# 공통 질문 풀 — 의도와 평가 포인트가 미리 정의되어 있음
# ============================================================

INTRO_QUESTION_ID = "C_INTRO"
CLOSING_QUESTION_ID = "C_CLOSING"                  # 기존 호환 — '하고 싶은 말'
# 마지막 질문 위치로 갈 수 있는 모든 마무리 질문 ID 들
CLOSING_QUESTION_IDS = {"C_CLOSING", "C_CLOSING_QA"}


# 공통 질문 풀.
#   topic_tag : 같은 토픽의 질문이 한 면접에서 동시에 뽑히지 않도록 묶음.
#               (예: 강점/약점 변형 4개는 self_strength_weakness 로 묶임)
#   position  : 'intro' / 'closing' 이면 무조건 첫/끝 질문으로 배치.
COMMON_QUESTIONS_POOL: List[Dict] = [
    # ── 인트로 ────────────────────────────────────────────
    {
        "question_id": INTRO_QUESTION_ID,
        "topic_tag": "intro",
        "position": "intro",
        "question": "간단한 자기소개 부탁드립니다.",
        "intent": "1분 내외로 본인의 핵심 경력·강점·지원 동기를 압축해 전달할 수 있는지 확인",
        "evaluation_points": [
            "1분 내외 분량으로 압축됐는가",
            "직무 관련 핵심 경험을 우선 언급했는가",
            "지원 동기와 자연스럽게 연결되는가",
        ],
    },

    # ── 마무리 (둘 중 하나만 노출되도록 같은 topic_tag) ──
    {
        "question_id": "C_CLOSING",
        "topic_tag": "closing",
        "position": "closing",
        "question": "마지막으로 하고 싶은 말이 있나요?",
        "intent": "면접 마무리에서 본인의 강점·의지를 임팩트 있게 전달하는지 확인",
        "evaluation_points": [
            "면접에서 다 못 한 어필 포인트를 추가하는가",
            "장황하지 않고 임팩트 있게 마무리하는가",
            "지원 의지를 진정성 있게 표현하는가",
        ],
    },
    {
        "question_id": "C_CLOSING_QA",
        "topic_tag": "closing",
        "position": "closing",
        "question": "마지막으로 궁금한 점 있으신가요?",
        "intent": "회사·직무에 대한 관심도와 사전 조사 수준을 확인",
        "evaluation_points": [
            "질문이 회사·직무에 대한 사전 조사를 반영하는가",
            "면접관이 답하기 적절한 범위의 질문인가",
            "지원자의 진정한 관심이 드러나는가",
        ],
    },

    # ── 학창 / 프로젝트 ──────────────────────────────────
    {
        "question_id": "C_SCHOOL_PROJECT",
        "topic_tag": "school_project",
        "question": "학창 시절 진행했던 프로젝트에 대해 말해주세요.",
        "intent": "학창 시절 직접 참여한 프로젝트에서 본인의 역할과 결과를 구체적으로 설명할 수 있는지 확인",
        "evaluation_points": [
            "프로젝트 목표와 본인 역할이 명확한가",
            "팀 기여와 본인 기여를 구분하여 설명하는가",
            "결과나 배운 점이 구체적인가",
        ],
    },

    # ── 전공 ─────────────────────────────────────────────
    {
        "question_id": "C_MAJOR_REASON",
        "topic_tag": "major_background",
        "question": "전공을 선택한 이유는 무엇인가요?",
        "intent": "진로 의사결정 근거와 전공 학습에 대한 진지도를 평가",
        "evaluation_points": [
            "선택의 동기가 표면적이지 않고 구체적인가",
            "전공 경험과 직무 방향이 자연스럽게 연결되는가",
            "본인의 가치관이나 관심사가 드러나는가",
        ],
    },
    {
        "question_id": "C_MAJOR_INTEREST",
        "topic_tag": "major_background",
        "question": "전공에서 가장 관심 있었던 과목과 그 이유를 말해주세요.",
        "intent": "학문적 관심 영역과 깊이 있는 학습 경험을 확인",
        "evaluation_points": [
            "특정 과목과 그 이유를 구체적으로 제시하는가",
            "단순 호기심을 넘어 학습 과정과 결과를 설명하는가",
            "직무·관심사와 연결되는 점이 드러나는가",
        ],
    },
    {
        "question_id": "C_MAJOR_JOB_FIT",
        "topic_tag": "major_job_fit",
        "question": "전공과 직무의 관련성을 설명해주세요.",
        "intent": "전공에서 얻은 지식·경험이 지원 직무와 어떻게 연결되는지 판단",
        "evaluation_points": [
            "전공 경험 중 직무와 직접 닿는 부분을 구체적으로 짚는가",
            "전공이 직무 수행에 미치는 영향을 논리적으로 설명하는가",
            "단순 학점·이수 과목 나열을 넘어 활용 방식을 제시하는가",
        ],
    },

    # ── 자기 인식: 강점/약점 (같은 토픽으로 묶음) ──────
    {
        "question_id": "C_PROS_CONS",
        "topic_tag": "self_strength_weakness",
        "question": "본인의 장점과 단점을 한 가지씩 말씀해 주세요.",
        "intent": "자기 인식 능력과 단점에 대한 보완 노력을 평가",
        "evaluation_points": [
            "장점이 직무 관련성을 가지는가",
            "장점을 뒷받침하는 구체적 사례가 있는가",
            "단점을 솔직히 인정하면서도 보완 노력을 함께 제시하는가",
        ],
    },
    {
        "question_id": "C_STRENGTH_WEAKNESS",
        "topic_tag": "self_strength_weakness",
        "question": "본인의 강점과 약점은 무엇인가요?",
        "intent": "자기 인식 능력과 직무 적합성, 개선 의지를 평가",
        "evaluation_points": [
            "강점이 직무와 관련성을 가지는가",
            "강점을 뒷받침하는 구체적 사례가 있는가",
            "약점을 솔직히 인정하면서도 보완 노력을 함께 제시하는가",
        ],
    },
    {
        "question_id": "C_STRENGTH_ONLY",
        "topic_tag": "self_strength_weakness",
        "question": "본인의 강점은 무엇인가요?",
        "intent": "직무 수행에 도움이 되는 강점을 식별하고 입증할 수 있는지 평가",
        "evaluation_points": [
            "직무 관련성 있는 강점을 제시하는가",
            "구체적 경험·사례로 뒷받침하는가",
            "강점이 결과·성과로 이어진 사례가 있는가",
        ],
    },
    {
        "question_id": "C_WEAKNESS_ONLY",
        "topic_tag": "self_strength_weakness",
        "question": "본인의 단점은 무엇인가요?",
        "intent": "약점 인식과 개선 노력, 자기 성찰 능력을 평가",
        "evaluation_points": [
            "단점을 솔직하게 인정하는가",
            "회피하거나 강점으로 포장하지 않는가",
            "보완 노력과 성과를 구체적으로 제시하는가",
        ],
    },

    # ── 차별점 (자기 어필) ─────────────────────────────
    {
        "question_id": "C_DIFFERENTIATION",
        "topic_tag": "self_differentiation",
        "question": "본인의 차별점은 무엇이라 생각하나요?",
        "intent": "다른 지원자 대비 본인의 고유한 강점과 인식을 확인",
        "evaluation_points": [
            "타인과 구분되는 구체적 경험·역량을 제시하는가",
            "그 차별점이 직무 가치로 연결되는가",
            "과장 없이 사실에 기반해 설명하는가",
        ],
    },

    # ── 미래 비전 ─────────────────────────────────────
    {
        "question_id": "C_FUTURE",
        "topic_tag": "future_vision",
        "question": "입사 후 5년 뒤 본인의 모습은 어떻게 그리고 있나요?",
        "intent": "장기 비전과 회사·직무와의 정합성을 평가",
        "evaluation_points": [
            "구체적이고 실행 가능한 목표를 제시하는가",
            "회사·직무 성장 경로와 정합성이 있는가",
            "현재 역량과 미래 목표 사이의 학습 계획이 드러나는가",
        ],
    },
    {
        "question_id": "C_LIFE_GOAL",
        "topic_tag": "future_vision",
        "question": "인생의 목표는 무엇인가요?",
        "intent": "장기 가치관과 동기 부여 요인을 파악",
        "evaluation_points": [
            "목표가 구체적이고 실행 가능한가",
            "본인의 경험과 일관되는가",
            "직무·회사와 어떻게 연결되는지 드러나는가",
        ],
    },
    {
        "question_id": "C_ASPIRATION",
        "topic_tag": "aspiration",
        "question": "입사 후 포부가 무엇인가요?",
        "intent": "장기적 기여 의지와 성장 계획을 확인",
        "evaluation_points": [
            "회사 비전과 본인 목표의 연결성이 있는가",
            "단·중·장기 계획이 균형 있는가",
            "회사에 어떤 가치를 가져올지가 구체적인가",
        ],
    },

    # ── 직업관/태도 ───────────────────────────────────
    {
        "question_id": "C_VOCATION",
        "topic_tag": "vocation",
        "question": "본인의 직업관은 무엇인가요?",
        "intent": "일과 경력에 대한 가치관, 회사 문화 정합성을 평가",
        "evaluation_points": [
            "직업의 의미를 본인 언어로 설명하는가",
            "수동적이지 않고 주체적인 관점이 드러나는가",
            "회사 가치와 연결되는 부분이 있는가",
        ],
    },
    {
        "question_id": "C_WORK_PHILOSOPHY",
        "topic_tag": "work_attitude",
        "question": "회사 생활에서 가장 중요하다고 생각하는 것은 무엇인가요?",
        "intent": "직장 가치관과 조직 적합성을 평가",
        "evaluation_points": [
            "본인 가치관을 명확히 표현하는가",
            "이상적 가치와 현실적 균형을 보이는가",
            "회사 문화와 연결되는 점이 있는가",
        ],
    },
    {
        "question_id": "C_WORK_ATTITUDE",
        "topic_tag": "work_attitude",
        "question": "입사한다면 어떤 자세로 업무에 임하겠나요?",
        "intent": "업무 태도와 학습 의지, 책임감을 평가",
        "evaluation_points": [
            "구체적인 행동 원칙을 제시하는가",
            "단순 다짐이 아닌 본인 경험에서 우러난 자세인가",
            "팀·회사에 기여하려는 의지가 드러나는가",
        ],
    },

    # ── 스트레스/역경/실패 ───────────────────────────
    {
        "question_id": "C_STRESS",
        "topic_tag": "stress",
        "question": "스트레스는 어떻게 해소하나요?",
        "intent": "스트레스 관리 능력과 자기 관리 습관을 확인",
        "evaluation_points": [
            "구체적인 해소 방법을 1~2개 제시하는가",
            "감정·신체·시간 관리 측면이 균형 있는가",
            "지속 가능하고 건강한 방식인가",
        ],
    },
    {
        "question_id": "C_HARDSHIP",
        "topic_tag": "hardship",
        "question": "살면서 가장 힘들었던 경험과 극복 방법을 말해주세요.",
        "intent": "역경 극복 경험과 회복 탄력성을 평가",
        "evaluation_points": [
            "상황의 맥락과 본인의 실제 어려움이 구체적인가",
            "극복 과정에서 본인의 의사결정과 행동이 드러나는가",
            "그 경험을 통해 얻은 교훈과 변화를 명확히 제시하는가",
        ],
    },
    {
        "question_id": "C_FAILURE",
        "topic_tag": "failure",
        "question": "인생에서 실패했던 경험을 말해주세요.",
        "intent": "실패에 대한 자기 인식, 회고 능력, 재도전 의지를 평가",
        "evaluation_points": [
            "실패 상황과 본인의 책임을 솔직히 설명하는가",
            "실패의 원인을 구체적으로 분석하는가",
            "이후 변화한 행동이나 배운 점이 명확한가",
        ],
    },

    # ── 갈등 (변형 셋, 한 토픽으로 묶음) ─────────────
    {
        "question_id": "C_CONFLICT",
        "topic_tag": "conflict",
        "question": "팀 내에서 갈등이 있었던 경험과 해결 과정을 말씀해 주세요.",
        "intent": "협업 능력과 갈등 관리 역량 평가",
        "evaluation_points": [
            "갈등 상황을 객관적으로 묘사하는가",
            "본인의 역할과 행동이 구체적으로 드러나는가",
            "결과와 배운 점이 명확한가",
            "타인을 비난하지 않고 균형 있게 서술하는가",
        ],
    },
    {
        "question_id": "C_CONFLICT_RESOLVED",
        "topic_tag": "conflict",
        "question": "갈등을 해결한 경험이 있나요?",
        "intent": "갈등 관리 능력과 협업 방식, 의사소통 역량을 평가",
        "evaluation_points": [
            "갈등 상황을 객관적으로 묘사하는가",
            "본인의 역할과 행동이 구체적으로 드러나는가",
            "결과와 배운 점이 명확한가",
        ],
    },
    {
        "question_id": "C_CONFLICT_HAS",
        "topic_tag": "conflict",
        "question": "누군가와 갈등했던 경험이 있나요?",
        "intent": "대인 갈등 상황에서의 대응 방식과 협업 역량을 확인",
        "evaluation_points": [
            "갈등의 원인과 양측의 입장을 객관적으로 설명하는가",
            "본인의 감정 관리와 의사소통 방식이 드러나는가",
            "갈등 이후 관계나 결과가 어떻게 변화했는지 제시하는가",
        ],
    },

    # ── 열정 / 기억에 남는 순간 ───────────────────────
    {
        "question_id": "C_PASSION",
        "topic_tag": "passion_project",
        "question": "최근에 가장 열정을 쏟았던 프로젝트나 경험을 소개해 주세요.",
        "intent": "주도적 학습·실행 경험과 그 깊이를 평가",
        "evaluation_points": [
            "프로젝트 목표와 본인의 동기가 명확한가",
            "본인이 주도적으로 기여한 부분이 드러나는가",
            "결과 또는 배운 점이 구체적으로 제시되는가",
        ],
    },
    {
        "question_id": "C_PASSION_PEAK",
        "topic_tag": "passion_peak",
        "question": "인생에서 가장 열정적이었던 순간은 언제였나요?",
        "intent": "내적 동기와 몰입 경험, 진정성을 확인",
        "evaluation_points": [
            "열정의 대상과 그 이유가 구체적인가",
            "몰입 과정에서의 행동·결과가 드러나는가",
            "그 경험이 본인의 성장에 어떤 영향을 주었는지 제시하는가",
        ],
    },
    {
        "question_id": "C_MEMORABLE",
        "topic_tag": "memorable_moment",
        "question": "인생에서 가장 기억에 남는 순간은 언제인가요?",
        "intent": "지원자의 가치관과 의미 부여 방식을 확인",
        "evaluation_points": [
            "순간의 맥락과 본인이 느낀 점이 구체적인가",
            "왜 의미 있는지 설명할 수 있는가",
            "현재 본인 가치관·태도에 미친 영향이 드러나는가",
        ],
    },
    {
        "question_id": "C_FRIEND_VIEW",
        "topic_tag": "social_perception",
        "question": "친구들은 본인을 어떤 사람이라고 이야기하나요?",
        "intent": "자기 인식과 사회적 관계에서의 모습을 확인 (타인 시각 활용)",
        "evaluation_points": [
            "타인의 시각을 구체적 표현으로 전달하는가",
            "긍정·부정 측면이 균형 있는가",
            "자기소개와 일관성 있는가",
        ],
    },

    # ── 회사 동기 ─────────────────────────────────────
    {
        "question_id": "C_MOTIVATION",
        "topic_tag": "company_motivation",
        "question": "저희 회사(또는 직무)에 지원하신 동기는 무엇인가요?",
        "intent": "회사·직무에 대한 이해도와 본인 가치관과의 연결성 평가",
        "evaluation_points": [
            "회사·직무에 대한 구체적 이해를 보여주는가",
            "본인 경험과 회사 방향성을 연결하는가",
            "장기적 기여 의지가 드러나는가",
        ],
    },
    {
        "question_id": "C_WHY_COMPANY",
        "topic_tag": "company_motivation",
        "question": "왜 우리 회사에 지원했나요?",
        "intent": "회사에 대한 이해도와 지원 동기의 진정성을 평가",
        "evaluation_points": [
            "회사·산업에 대한 구체적 이해를 보여주는가",
            "본인 경험·관심사와 회사를 연결하는가",
            "막연한 호감이 아닌 의지가 드러나는가",
        ],
    },
    {
        "question_id": "C_PREPARATION",
        "topic_tag": "company_motivation",
        "question": "우리 회사에 입사하기 위해 무엇을 준비했나요?",
        "intent": "지원에 대한 사전 준비 수준과 적극성을 확인",
        "evaluation_points": [
            "구체적인 준비 활동(학습·프로젝트·자격 등)을 제시하는가",
            "회사·직무 요구사항을 인지하고 준비했는가",
            "준비 과정의 깊이와 지속성이 드러나는가",
        ],
    },
    {
        "question_id": "C_HOW_KNOW",
        "topic_tag": "how_know_company",
        "question": "회사를 알게 된 계기는 무엇인가요?",
        "intent": "지원 동기의 자연스러움과 회사 관심도의 시작점을 확인",
        "evaluation_points": [
            "계기가 구체적이고 진정성 있는가",
            "이후 관심이 지속·심화된 과정이 드러나는가",
            "단순 정보 검색을 넘어선 접점이 있는가",
        ],
    },
    {
        "question_id": "C_VALUES_FIT",
        "topic_tag": "values_fit",
        "question": "회사의 인재상 중 어떤 점이 본인과 부합한다고 생각하나요?",
        "intent": "회사 가치관에 대한 이해와 본인 정합성을 평가",
        "evaluation_points": [
            "회사 인재상을 정확히 인지하는가",
            "본인 경험·성격과 연결해 설명하는가",
            "단순 인용이 아닌 본인 사례를 동반하는가",
        ],
    },

    # ── 본인 가치 제안 ────────────────────────────────
    {
        "question_id": "C_WHY_HIRE",
        "topic_tag": "why_hire",
        "question": "우리가 본인을 뽑아야 하는 이유는 무엇인가요?",
        "intent": "자기 어필 능력과 직무 가치 제안 능력을 평가",
        "evaluation_points": [
            "타 지원자 대비 차별화된 가치를 제시하는가",
            "본인 경험·역량을 직무 요구와 연결하는가",
            "근거를 뒷받침하는 구체적 사례를 제시하는가",
        ],
    },
    {
        "question_id": "C_CONTRIBUTION_HELP",
        "topic_tag": "contribution",
        "question": "본인이 회사에 어떤 도움을 줄 수 있다고 생각하나요?",
        "intent": "본인 역량과 회사 가치 창출의 연결고리를 확인",
        "evaluation_points": [
            "본인의 경험·역량을 회사 비즈니스에 매핑하는가",
            "구체적 기여 방식을 제시하는가",
            "현실적 수준에서 약속 가능한 범위를 제시하는가",
        ],
    },
    {
        "question_id": "C_CONTRIBUTION_VALUE",
        "topic_tag": "contribution",
        "question": "본인이 우리 회사에 어떤 기여를 할 수 있나요?",
        "intent": "자신의 역량을 회사 가치 창출과 연결할 수 있는지 평가",
        "evaluation_points": [
            "본인 경험·역량을 회사 비즈니스와 연결하여 설명하는가",
            "구체적인 기여 방식을 제시하는가",
            "단기·장기 관점이 균형 있는가",
        ],
    },

    # ── 회사 컨텍스트 / 사전 조사 ─────────────────────
    {
        "question_id": "C_RECENT_ISSUE",
        "topic_tag": "company_knowledge",
        "question": "회사의 최근 이슈에 대해 찾아본 것이 있나요?",
        "intent": "회사·업계 이해도와 관심 깊이를 확인",
        "evaluation_points": [
            "최근 이슈를 정확히 알고 있는가",
            "이슈에 대한 본인의 견해를 제시하는가",
            "단순 인용이 아닌 본인 해석을 더하는가",
        ],
    },
    {
        "question_id": "C_OTHER_INTERVIEWS",
        "topic_tag": "other_interviews",
        "question": "다른 기업 면접도 보고 있나요?",
        "intent": "지원 우선순위와 솔직성, 직무 일관성을 확인",
        "evaluation_points": [
            "솔직하게 답하는가",
            "다른 지원도 본인 커리어 방향과 일관되는가",
            "본 회사에 대한 우선순위·관심을 잃지 않는가",
        ],
    },

    # ── 근무 조건 (각각 다른 토픽) ────────────────────
    {
        "question_id": "C_REGIONAL",
        "topic_tag": "location_regional",
        "question": "지방 근무가 가능한가요?",
        "intent": "근무지 유연성과 본인 우선순위를 확인",
        "evaluation_points": [
            "솔직하고 명확하게 답하는가",
            "본인 상황과 의지를 균형 있게 설명하는가",
            "회피하거나 막연하게 답하지 않는가",
        ],
    },
    {
        "question_id": "C_OVERSEAS",
        "topic_tag": "location_overseas",
        "question": "해외 근무를 하게 되어도 괜찮은가요?",
        "intent": "글로벌 적응력과 본인 우선순위를 확인",
        "evaluation_points": [
            "솔직하고 명확하게 답하는가",
            "본인 경험·역량과 연결해 설명하는가",
            "준비 의지와 조건을 균형 있게 제시하는가",
        ],
    },
    {
        "question_id": "C_WORK_INTENSITY",
        "topic_tag": "work_intensity",
        "question": "업무 강도가 센 편입니다. 괜찮은가요?",
        "intent": "업무 강도에 대한 수용성과 자기 관리 능력을 확인",
        "evaluation_points": [
            "솔직하게 답하는가",
            "강도 높은 업무 경험을 사례로 제시하는가",
            "지속 가능한 자기 관리 방식을 설명하는가",
        ],
    },
    {
        "question_id": "C_BUSINESS_TRIP",
        "topic_tag": "business_trip",
        "question": "출장이 잦을 수 있는데 어떻게 생각하나요?",
        "intent": "근무 조건 유연성과 적응력을 확인",
        "evaluation_points": [
            "솔직하고 명확하게 답하는가",
            "본인 상황과 직무 요구를 균형 있게 설명하는가",
            "회피하거나 막연하게 답하지 않는가",
        ],
    },
    {
        "question_id": "C_OVERTIME",
        "topic_tag": "overtime",
        "question": "상사가 주말 근무나 야근을 지시한다면 어떻게 하겠나요?",
        "intent": "업무 헌신도와 적절한 경계 인식을 평가",
        "evaluation_points": [
            "업무 우선순위와 본인 한계를 균형 있게 표현하는가",
            "필요할 때 헌신할 수 있다는 의지를 보이는가",
            "지속 가능성과 효율성도 고려하는가",
        ],
    },

    # ── 직무 적합성 ───────────────────────────────────
    {
        "question_id": "C_WHY_ROLE",
        "topic_tag": "role_motivation",
        "question": "해당 직무에 지원한 이유는 무엇인가요?",
        "intent": "직무에 대한 이해도와 동기를 평가",
        "evaluation_points": [
            "직무의 본질·역할을 이해하고 있는가",
            "본인 경험과 직무 요구사항을 연결하는가",
            "단순 호기심을 넘어선 진로 동기가 드러나는가",
        ],
    },
    {
        "question_id": "C_ROLE_SKILL",
        "topic_tag": "role_skill_needed",
        "question": "해당 직무에 필요한 역량은 무엇이라 생각하나요?",
        "intent": "직무 이해도와 역량 자기 진단 능력을 평가",
        "evaluation_points": [
            "주요 역량을 정확히 짚는가",
            "그 역량을 본인이 어떻게 갖추고 있는지 사례로 설명하는가",
            "현재 부족한 점과 보완 계획도 인지하는가",
        ],
    },
    {
        "question_id": "C_ROLE_CHANGE",
        "topic_tag": "role_flexibility",
        "question": "직무가 바뀌어도 괜찮은가요?",
        "intent": "유연성과 본인 지원 동기의 깊이를 확인",
        "evaluation_points": [
            "솔직하고 일관성 있게 답하는가",
            "특정 직무에 대한 의지와 유연성의 균형을 보이는가",
            "본인 커리어 비전과 연결되는가",
        ],
    },
    {
        "question_id": "C_FIELD_KNOWLEDGE",
        "topic_tag": "field_knowledge",
        "question": "지원 분야에 대한 지식과 향후 발전 방향을 말해주세요.",
        "intent": "분야에 대한 전문 지식과 미래 인식을 평가",
        "evaluation_points": [
            "분야의 핵심 개념과 현황을 설명할 수 있는가",
            "최근 트렌드와 변화 방향을 인지하는가",
            "본인의 학습 계획과 연결되는가",
        ],
    },
    {
        "question_id": "C_WANTED_TASK",
        "topic_tag": "wanted_task",
        "question": "입사 후 하고 싶은 업무는 무엇인가요?",
        "intent": "구체적 직무 이해와 자기 동기를 확인",
        "evaluation_points": [
            "구체적 업무 영역을 짚는가",
            "본인 경험·강점과 연결되는가",
            "현실적이고 단계적인 계획인가",
        ],
    },
    {
        "question_id": "C_ROLE_TRAIT_FIT",
        "topic_tag": "role_trait_fit",
        "question": "해당 직무에 필요한 자질 중 어떤 점이 본인과 부합한다고 생각하나요?",
        "intent": "직무 요구사항에 대한 자기 평가와 사례를 확인",
        "evaluation_points": [
            "요구되는 자질을 정확히 인지하는가",
            "본인 경험에서 그 자질이 드러난 사례를 제시하는가",
            "과장 없이 사실에 기반해 설명하는가",
        ],
    },
    {
        "question_id": "C_ROLE_STRENGTH",
        "topic_tag": "role_strength",
        "question": "직무와 관련된 본인의 강점은 무엇인가요?",
        "intent": "직무 적합성에 핵심이 되는 본인의 강점을 평가",
        "evaluation_points": [
            "직무와 직접 연결되는 강점을 제시하는가",
            "구체적 경험·결과로 뒷받침하는가",
            "팀·회사에 어떤 영향을 줄 수 있는지 설명하는가",
        ],
    },
    {
        "question_id": "C_WHEN_KNEW_ROLE",
        "topic_tag": "when_knew_role",
        "question": "해당 직무를 언제 알게 되었나요?",
        "intent": "직무 관심의 출발점과 진정성을 확인",
        "evaluation_points": [
            "계기와 시점이 구체적인가",
            "이후 관심이 어떻게 발전했는지 설명하는가",
            "표면적 정보가 아닌 본인 경험과 연결되는가",
        ],
    },
    {
        "question_id": "C_ROLE_ISSUE",
        "topic_tag": "role_issue",
        "question": "직무와 관련해 최근 관심 있는 이슈를 설명해주세요.",
        "intent": "직무 영역에 대한 학습 지속성과 분석 능력을 평가",
        "evaluation_points": [
            "관련 분야의 최근 이슈를 정확히 짚는가",
            "본인의 견해를 구체적으로 제시하는가",
            "단순 정보 전달을 넘어 함의·영향을 설명하는가",
        ],
    },

    # ── 상황 대응 ─────────────────────────────────────
    {
        "question_id": "C_BOSS_CONFLICT",
        "topic_tag": "boss_conflict",
        "question": "상사와 의견이 다를 때 어떻게 대처하겠나요?",
        "intent": "갈등 상황 대응과 의사소통 방식을 평가",
        "evaluation_points": [
            "감정이 아닌 사실·근거 기반으로 대화하는가",
            "상사 의견을 존중하면서도 본인 의견을 명확히 전달하는가",
            "조직 의사결정 구조를 이해하고 있는가",
        ],
    },
    {
        "question_id": "C_UNFAIR_ORDER",
        "topic_tag": "unfair_order",
        "question": "상사가 부당한 업무 지시를 한다면 어떻게 하겠나요?",
        "intent": "윤리적 판단력과 적절한 대응 방식을 확인",
        "evaluation_points": [
            "원칙과 현실 사이의 균형을 잡는가",
            "공식적 절차와 의사소통 방식을 인지하는가",
            "감정적 반응을 자제하고 구조적으로 접근하는가",
        ],
    },
    {
        "question_id": "C_CUSTOMER_COMPLAINT",
        "topic_tag": "customer_complaint",
        "question": "고객이 불만을 제기하면 어떻게 대처하겠나요?",
        "intent": "고객 응대 능력과 문제 해결 사고를 평가",
        "evaluation_points": [
            "상황 파악 → 공감 → 해결의 단계가 드러나는가",
            "감정적 대응을 자제하고 사실 기반으로 접근하는가",
            "조직 자원 활용과 사후 개선까지 고려하는가",
        ],
    },
    {
        "question_id": "C_VENDOR_CONFLICT",
        "topic_tag": "vendor_conflict",
        "question": "거래처와 갈등이 생기면 어떻게 대처하겠나요?",
        "intent": "B2B 관계 관리 능력과 갈등 해결 방식을 평가",
        "evaluation_points": [
            "상황 파악과 양측 이해관계 인지가 드러나는가",
            "단기 해결과 장기 관계 유지의 균형을 잡는가",
            "조직 차원의 대응까지 고려하는가",
        ],
    },

    # ─────────────────────────────────────────────────
    # ※ 상황 의존 질문 (학력 공백/낮은 학점/전공 불일치 등)
    #    이력서 컨텍스트가 있어야 의미가 있어 더미로만 보관.
    #    실제 활성화는 별도 컨텍스트 매칭 로직 구현 후 진행.
    # ─────────────────────────────────────────────────
    # {"question_id":"C_GAP_PERIOD",     "question":"재학 시절 ○년간의 공백이 있는데 이 기간 동안 무엇을 했나요?"},
    # {"question_id":"C_LOW_GPA",        "question":"학점이 좋지 않은데 이유가 무엇인가요?"},
    # {"question_id":"C_MAJOR_MISMATCH", "question":"지원 분야와 전공이 맞지 않는데 지원한 이유는 무엇인가요?"},
    # {"question_id":"C_LONG_SCHOOL",    "question":"학교를 오래 다닌 편인데 특별한 이유가 있나요?"},
    # {"question_id":"C_LOW_SEMESTER",   "question":"O학기 성적이 다소 낮은데 이유가 무엇인가요?"},
]


# ============================================================
# 맞춤 질문 — OpenAI 가 질문 + 의도 + 평가 포인트를 함께 생성
# ============================================================

SYSTEM_PROMPT = (
    "당신은 한국어 면접관입니다. 지원자의 이력서를 읽고, "
    "지원자의 경험·기술·프로젝트에 근거한 구체적이고 깊이 있는 면접 질문을 만들어 주세요.\n\n"
    "각 질문 객체에는 반드시 다음 세 필드가 포함되어야 합니다:\n"
    "  - question         : 한 문장의 정중한 존댓말 질문\n"
    "  - intent           : 이 질문으로 평가하려는 핵심을 1~2문장으로 명시\n"
    "  - evaluation_points: 답변에서 확인하고 싶은 포인트 3~4개 (각각 한 문장의 평가 기준)\n\n"
    "출력은 반드시 다음 JSON 형식으로만 답하세요:\n"
    "{\n"
    "  \"questions\": [\n"
    "    {\n"
    "      \"question\": \"...\",\n"
    "      \"intent\": \"...\",\n"
    "      \"evaluation_points\": [\"...\", \"...\", \"...\"]\n"
    "    }\n"
    "  ]\n"
    "}"
)


def generate_personalized_questions(
    resume_text: str,
    n: int = 5,
    model: str = "gpt-4o-mini",
    api_key: str | None = None,
) -> List[Dict]:
    """이력서 기반 맞춤 질문 n개를 생성. 각 질문에 의도/평가 포인트 포함."""
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    user_prompt = (
        f"다음은 지원자의 이력서입니다.\n\n---\n{resume_text}\n---\n\n"
        f"위 이력서에 근거하여 면접 질문 {n}개를 생성해 주세요. "
        f"각 질문에 question, intent, evaluation_points 를 모두 포함해야 합니다."
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
    raw_questions = data.get("questions", [])

    # 형식 검증·정리
    cleaned: List[Dict] = []
    for i, item in enumerate(raw_questions, start=1):
        if not isinstance(item, dict):
            continue
        q_text = (item.get("question") or "").strip()
        if not q_text:
            continue
        intent = (item.get("intent") or "").strip()
        ep = item.get("evaluation_points") or []
        if not isinstance(ep, list):
            ep = [str(ep)]
        ep = [str(p).strip() for p in ep if str(p).strip()]
        cleaned.append({
            "question_id": f"P{i:02d}",
            "question": q_text,
            "intent": intent,
            "evaluation_points": ep,
        })
    return cleaned


def pick_common_questions(n: int = 2) -> List[Dict]:
    """
    공통 질문 풀에서 topic_tag 기준으로 중복 없이 n개 추출.

    같은 topic_tag 안에 있는 질문(예: 강점/약점 변형 4개, 갈등 변형 3개,
    마무리 변형 2개)에서는 한 면접 안에 하나만 뽑힘.
    n 이 사용 가능한 토픽 수보다 크면 가용 토픽 수만큼만 반환.
    """
    if n <= 0:
        return []
    by_topic: Dict[str, List[Dict]] = {}
    for q in COMMON_QUESTIONS_POOL:
        tag = q.get("topic_tag") or q["question_id"]
        by_topic.setdefault(tag, []).append(q)

    topics = list(by_topic.keys())
    random.shuffle(topics)
    picked: List[Dict] = []
    for tag in topics:
        if len(picked) >= n:
            break
        picked.append(random.choice(by_topic[tag]))
    return picked


def build_interview_questions(
    resume_text: str,
    n_personalized: int = 5,
    n_common: int = 2,
    shuffle: bool = False,
) -> List[Dict]:
    """
    최종 면접 질문 리스트(dict 형태) 생성.

    순서 규칙:
      - 공통 질문에서 자기소개(C_INTRO) 가 뽑히면 → 무조건 맨 앞
      - 공통 질문에서 마무리(C_CLOSING* 계열) 가 뽑히면 → 무조건 맨 끝
        (CLOSING_QUESTION_IDS 안의 질문들은 같은 topic_tag='closing' 이라
         pick_common_questions 단계에서 동시에 두 개가 뽑히지 않음)
      - 그 외 중간 공통 + 맞춤 질문은 사이에 배치
      - shuffle=True 라도 두 앵커 위치는 흔들리지 않음
    """
    common = pick_common_questions(n_common)
    personalized = generate_personalized_questions(resume_text, n=n_personalized)

    intro = next((q for q in common if q["question_id"] == INTRO_QUESTION_ID), None)
    closings = [q for q in common if q["question_id"] in CLOSING_QUESTION_IDS]
    middle_common = [
        q for q in common
        if q["question_id"] != INTRO_QUESTION_ID
        and q["question_id"] not in CLOSING_QUESTION_IDS
    ]

    middle = middle_common + personalized
    if shuffle:
        random.shuffle(middle)

    questions: List[Dict] = []
    if intro is not None:
        questions.append(intro)
    questions.extend(middle)
    questions.extend(closings)
    return questions


# ============================================================
# Async / 분할 파이프라인 (Phase 3 + Phase 4)
# ============================================================

# ============================================================
# 기본 프롬프트 (gpt-4o-mini 등 빠른 모델)
# ============================================================

# 자소서 사전 요약용 프롬프트 (Phase 3)
SUMMARY_SYSTEM_PROMPT = (
    "당신은 한국어 면접 준비 보조자입니다. "
    "지원자의 자기소개서를 1500자 이내로 핵심만 추려서 요약하세요. "
    "학력 / 경력 / 프로젝트 / 기술 스택 / 핵심 성과 / 지원 동기 위주로, "
    "원문의 정량적 표현(숫자·기간·기술명)은 가능한 한 보존합니다. "
    "출력은 자연스러운 한국어 산문 형태."
)

# 질문 텍스트만 빠르게 생성 (Phase 4 Step 1)
QUICK_QUESTION_SYSTEM_PROMPT = (
    "당신은 한국어 면접관입니다. 지원자의 이력서를 읽고 면접 질문을 만드세요. "
    "질문은 한국어 한 문장의 정중한 존댓말로, 지원자의 경험·기술·프로젝트에 근거해야 합니다. "
    "출력은 반드시 다음 JSON 형식: {\"questions\": [\"질문1\", \"질문2\", ...]}"
)

# 단일 질문 메타 보강 (Phase 4 Step 2)
ENRICH_SYSTEM_PROMPT = (
    "당신은 한국어 면접 평가자입니다. 주어진 면접 질문에 대해 "
    "(1) 이 질문으로 평가하려는 의도(intent)를 1~2문장으로, "
    "(2) 답변에서 확인하고 싶은 평가 포인트 3~4개를 각각 한 문장으로 작성하세요.\n\n"
    "중요 — 공통 기준과 중복 금지:\n"
    "다음 6항목은 시스템이 모든 답변에 자동으로 매기는 공통 점수입니다. "
    "evaluation_points 는 이 6항목과 의미적으로 겹치지 않는, **이 질문 고유의 내용(WHAT) 검증 포인트** 만 작성하세요.\n"
    "  1) 질문 의도 파악  2) 답변 구조성  3) 이력서/직무 관련성  "
    "4) 경험의 구체성  5) 논리성과 설득력  6) 표현의 간결성\n\n"
    "금지 예 (공통과 중복):\n"
    "- \"답변이 구체적인가\"  - \"논리적으로 설명하는가\"  - \"직무와 관련 있는가\"  "
    "- \"본인 역할이 명확한가\"  - \"결론·근거·사례 구조인가\"\n\n"
    "권장 예 (질문 고유 내용 검증):\n"
    "- \"PERCLOS 와 EAR 임계값 방식의 신뢰도 차이를 인지하는가\"\n"
    "- \"Redis 와 Memcached 선택 기준에서 데이터 영속성을 다루는가\"\n"
    "- \"AMI 통신망 장애 시 물리계층과 네트워크계층 구분 기준을 제시하는가\"\n\n"
    "출력 JSON: {\"intent\": \"...\", \"evaluation_points\": [\"...\", \"...\", \"...\"]}"
)


# ============================================================
# 고급 프롬프트 (gpt-4o / gpt-5.4 / gpt-5.5)
# 시니어 면접관·HR 평가자 페르소나, 구조화 면접 설계 기준 강화
# ============================================================

ADVANCED_MODELS = {"gpt-4o", "gpt-5.4", "gpt-5.5"}


def _is_advanced(model: Optional[str]) -> bool:
    """이 모델에 대해 ADVANCED 프롬프트를 써야 하는지."""
    return (model or "") in ADVANCED_MODELS


SUMMARY_SYSTEM_PROMPT_ADVANCED = """
당신은 한국어 채용 면접을 준비하는 시니어 면접 설계자이자 HR 평가자입니다.

목표:
지원자의 자기소개서, 이력서, 경력기술서, 포트폴리오 내용을 바탕으로
면접관이 질문 설계와 평가 기준 수립에 바로 활용할 수 있는
'면접 사전 브리핑'을 1500자 이내의 자연스러운 한국어 산문으로 작성하세요.

요약 기준:
1. 학력, 경력, 프로젝트, 기술 스택, 역할, 성과, 지원 동기, 직무 적합성을 중심으로 요약하세요.
2. 원문에 있는 정량 표현은 최대한 보존하세요.
   예: 기간, 인원, 트래픽, 매출, 비용, 성능 개선율, 정확도, 처리량, 장애 건수, 기술명, 도구명, 버전, 자격증, 수상명.
3. 단순 나열이 아니라, '지원자가 어떤 문제를 어떤 방식으로 해결했고 어떤 결과를 냈는지'가 드러나도록 정리하세요.
4. 프로젝트별로 지원자의 실제 기여 범위가 드러나게 하세요.
   단, 원문에 없는 개인 기여율이나 성과를 추정하지 마세요.
5. 기술 스택은 단순 보유 목록이 아니라 실제 사용 맥락과 함께 압축하세요.
6. 지원 동기는 회사·직무·산업 이해, 성장 방향, 과거 경험과의 연결성을 중심으로 요약하세요.
7. 면접에서 추가 검증이 필요한 부분이 자연스럽게 드러나도록 하되,
   별도 제목을 붙여 공격적으로 지적하지는 마세요.
8. 원문에 없는 사실, 학력, 경력, 성과, 기술 숙련도, 인성 평가를 만들어내지 마세요.
9. 모호한 표현은 단정하지 말고 '경험을 제시했다', '기여한 것으로 설명했다', '강조했다'처럼 표현하세요.
10. 개인정보, 가족관계, 나이, 성별, 종교, 출신지역, 혼인 여부 등 직무와 무관한 민감 정보는 요약하지 마세요.

출력 형식:
- 1500자 이내
- 자연스러운 한국어 산문
- 과도한 미사여구 금지
- 면접관이 1~2분 안에 읽고 핵심을 파악할 수 있는 밀도 높은 문장
"""


QUICK_QUESTION_SYSTEM_PROMPT_ADVANCED = """
당신은 한국어 구조화 면접을 설계하는 시니어 면접관입니다.
지원자의 이력서, 자기소개서, 경력기술서, 포트폴리오 요약을 읽고
실제 면접에서 사용할 수 있는 고품질 질문을 생성하세요.

핵심 원칙:
1. 모든 질문은 지원자의 실제 경험, 프로젝트, 기술, 성과, 지원 동기 중 하나 이상에 근거해야 합니다.
2. 질문은 직무 성공 가능성을 평가하기 위한 것이어야 하며, 단순 호기심이나 일반 인성 질문을 만들지 마세요.
3. 질문은 다음 평가 범주를 균형 있게 포함하세요.
   - 직무 관련 지식과 기술 깊이
   - 문제 해결 방식과 의사결정 근거
   - 프로젝트에서의 실제 역할과 기여도
   - 성과의 측정 방식과 재현 가능성
   - 협업, 커뮤니케이션, 갈등 조정
   - 실패, 제약, 트레이드오프, 리스크 대응
   - 학습 능력과 개선 경험
   - 지원 동기와 직무·조직 적합성
4. 행동사례형 질문과 상황판단형 질문을 적절히 섞으세요.
   - 행동사례형: 과거 경험에서 실제로 무엇을 했고 어떤 결과가 있었는지 확인합니다.
   - 상황판단형: 유사하거나 더 어려운 상황에서 어떻게 판단하고 행동할지 확인합니다.
5. 기술 직무라면 단순 기술명 암기 질문을 피하고, 설계 선택, 병목 해결, 장애 대응, 데이터 검증, 보안, 테스트, 확장성, 유지보수성, 비용·성능 트레이드오프를 묻는 질문을 우선하세요.
6. 질문은 하나의 핵심 평가 의도만 담아야 하며, 한 문장 안에 여러 질문을 과도하게 섞지 마세요.
7. 질문은 정중한 한국어 존댓말 한 문장으로 작성하세요.
8. 이력서에 없는 사실을 전제로 질문하지 마세요.
9. 보호특성 또는 직무와 무관한 사적 정보에 관한 질문을 만들지 마세요.
   예: 나이, 성별, 가족관계, 결혼·출산 계획, 종교, 출신지역, 건강상태, 장애 여부, 정치 성향.
10. 너무 일반적인 질문은 금지합니다.
   예: "본인의 장단점은 무엇인가요?", "입사 후 포부는 무엇인가요?", "갈등을 어떻게 해결하시나요?"
   이런 질문이 필요하다면 반드시 지원자의 구체적 경험과 연결해 다시 작성하세요.

좋은 질문의 형태:
- "OO 프로젝트에서 XX를 개선했다고 하셨는데, 당시 병목의 원인을 어떻게 검증했고 어떤 대안들을 비교하셨는지 설명해 주시겠습니까?"
- "OO 기술을 선택하신 과정에서 성능, 개발 속도, 유지보수성 사이의 트레이드오프를 어떻게 판단하셨습니까?"
- "해당 성과가 개인 기여인지 팀 성과인지 구분하기 위해, 본인이 직접 맡은 의사결정과 실행 범위를 설명해 주시겠습니까?"

출력 규칙:
- 반드시 유효한 JSON만 출력하세요.
- 마크다운, 설명문, 주석, 코드블록을 출력하지 마세요.
- 출력 형식은 반드시 다음과 같습니다.
{
  "questions": [
    "질문1",
    "질문2"
  ]
}
"""


ENRICH_SYSTEM_PROMPT_ADVANCED_COMPAT = """
당신은 한국어 구조화 면접의 평가 기준을 설계하는 시니어 면접 평가자입니다.
주어진 면접 질문 하나에 대해 평가 의도와 평가 포인트를 작성하세요.

⚠ 가장 중요한 제약 — 공통 기준과 중복 금지:
다음 6항목은 시스템이 모든 답변에 대해 자동으로 매기는 공통 점수입니다(총 50점).
  1) 질문 의도 파악       9점
  2) 답변 구조성          9점
  3) 이력서/직무 관련성  13점
  4) 경험의 구체성        9점
  5) 논리성과 설득력      6점
  6) 표현의 간결성        4점

evaluation_points 는 위 6항목과 의미적으로 겹치지 않는,
**이 질문 고유의 내용(WHAT) 검증 포인트** 만 작성해야 합니다.
공통 6항목을 다시 풀어 쓴 것 같은 표현은 절대 금지입니다.

금지 예 (공통과 중복 → 같은 측면에 점수가 두 번 부여됨):
- "본인 역할과 의사결정 범위가 구체적으로 드러나는가"     → 4번 '경험의 구체성' 과 동일
- "결론·근거·사례·마무리가 정리되어 있는가"                → 2번 '답변 구조성' 과 동일
- "주장과 근거가 자연스럽게 연결되는가"                    → 5번 '논리성과 설득력' 과 동일
- "지원 직무와 연결되는가"                                  → 3번 '이력서/직무 관련성' 과 동일
- "결과를 정량·정성 지표로 검증하는가"                     → 4번 '경험의 구체성' 과 사실상 동일
- "답변이 구체적인가" / "논리적인가" / "직무와 관련 있는가" → 위와 동일

권장 예 (질문 고유 내용 — 이 질문이 아닌 다른 질문엔 적용 안 됨):
- (스마트그리드 통신망 질문) "AMI 의 PLC 와 RF 기술 차이, 운영 환경별 선택 근거를 구체적으로 짚는가"
- (졸음감지 프로젝트 질문) "PERCLOS 와 단순 EAR 임계값 방식의 신뢰도 차이를 인지하고, 임계값 캘리브레이션 필요성을 언급하는가"
- (캐시 설계 질문) "Redis 와 Memcached 의 선택 기준에서 데이터 영속성·persistence 트레이드오프를 다루는가"
- (마이크로서비스 질문) "서비스 간 통신 방식(동기 REST vs 비동기 메시지)의 장애 격리 영향을 설명하는가"
- (BMS 질문) "SoC 추정 알고리즘(쿨롱 카운팅 vs OCV 기반)의 오차 누적 특성을 인지하는가"

작성 원칙:
1. intent 는 이 질문이 어떤 직무 역량·행동 증거·리스크 검증을 노리는지 1~2문장으로 설명하세요.
2. evaluation_points 는 면접관이 답변을 듣고 실제로 관찰·기록할 수 있는 기준으로 작성하세요.
3. 평가 포인트는 3~4개, 각각 한 문장.
4. **모든 evaluation_point 는 이 질문에서만 나올 수 있는 고유 키워드·개념·기술명·시나리오를 포함**해야 합니다.
   질문 텍스트에서 핵심 명사·기술 용어·도메인 개념을 1개 이상 인용·변형해 사용하세요.
5. 추상적 인성 평가 표현 금지.
   금지 예: "성실한지 확인한다", "열정이 있는지 본다", "좋은 태도인지 평가한다"
6. 질문과 무관한 역량을 억지로 추가하지 마세요.
7. 이력서나 질문에 없는 사실을 평가 기준에 포함하지 마세요.
8. 보호특성, 사생활, 비직무 정보를 평가 기준으로 삼지 마세요.

자기 점검 (출력 직전):
각 evaluation_point 를 검사해, 공통 6항목 중 어느 하나라도 같은 의미라면 그 항목은 폐기하고
**질문 고유의 내용(키워드·기술·도메인)** 을 더 끌어와서 다시 작성하세요.

출력 규칙:
- 반드시 유효한 JSON만 출력하세요.
- 마크다운, 설명문, 주석, 코드블록을 출력하지 마세요.
- 출력 형식은 반드시 다음과 같습니다.
{
  "intent": "…",
  "evaluation_points": [
    "…",
    "…",
    "…"
  ]
}
"""


# 아래 두 프롬프트는 더 풍부한 평가 메타(루브릭·후속질문·신호)를 산출.
# 현재 DB 스키마(intent + evaluation_points)와 호환되지 않으므로 모듈 상수로만 보관.
# 추후 Question 모델에 follow_up_questions / rubric / signals 컬럼을 추가하면 wiring 가능.

ENRICH_SYSTEM_PROMPT_ADVANCED_FULL = """
당신은 한국어 구조화 면접의 평가지를 설계하는 시니어 면접 평가자입니다.
주어진 면접 질문 하나에 대해 실제 면접관이 사용할 수 있는 평가 메타데이터를 생성하세요.

목표:
질문이 단순 대화로 끝나지 않고, 면접관이 일관되게 평가·기록·비교할 수 있도록
평가 의도, 평가 역량, 관찰 포인트, 후속 질문, 행동기반 루브릭을 설계합니다.

작성 원칙:
1. 질문의 핵심 평가 역량을 하나 또는 두 개로 제한하세요.
2. intent는 이 질문이 왜 필요한지, 어떤 직무 성과 예측 신호를 보려는지 설명하세요.
3. evaluation_points는 답변에서 확인해야 할 관찰 가능한 기준으로 작성하세요.
4. follow_up_questions는 지원자의 답변이 모호할 때 깊이를 확인하는 꼬리질문으로 작성하세요.
5. positive_signals는 좋은 답변에서 나타나는 구체적 신호를 작성하세요.
6. negative_signals는 낮은 평가로 이어질 수 있는 구체적 신호를 작성하세요.
7. rubric은 1~4점 행동기준척도 형태로 작성하세요.
   - 1점: 부적합하거나 근거가 부족한 답변
   - 2점: 일부 경험은 있으나 구조와 근거가 약한 답변
   - 3점: 직무 수행에 충분한 구체성과 판단 근거가 있는 답변
   - 4점: 복잡한 제약 속에서도 높은 수준의 문제정의, 실행, 검증, 학습을 보여주는 답변
8. 루브릭은 성격이나 인상평이 아니라 답변 내용과 행동 증거에 기반해야 합니다.
9. 이력서나 질문에 없는 사실을 만들어내지 마세요.
10. 보호특성, 사생활, 비직무 정보를 평가하지 마세요.
11. 질문이 기술 질문이면 기술 선택의 근거, 대안 비교, 검증 방법, 운영 리스크, 재현 가능성을 포함하세요.
12. 질문이 협업 질문이면 이해관계자 조율, 갈등 원인 분석, 커뮤니케이션 방식, 결과와 회고를 포함하세요.
13. 질문이 동기 질문이면 회사 찬양이 아니라 직무 이해, 경험 연결성, 장기적 성장 방향, 현실적 기대 수준을 포함하세요.

출력 규칙:
- 반드시 유효한 JSON만 출력하세요.
- 마크다운, 설명문, 주석, 코드블록을 출력하지 마세요.
- 모든 문장은 한국어 존댓말 또는 평가 문체로 작성하세요.
- 출력 형식은 반드시 다음과 같습니다.

{
  "target_competency": "평가 역량명",
  "question_type": "behavioral | situational | technical_deep_dive | project_deep_dive | motivation | collaboration",
  "intent": "이 질문의 평가 의도",
  "evaluation_points": [
    "관찰 가능한 평가 포인트 1",
    "관찰 가능한 평가 포인트 2",
    "관찰 가능한 평가 포인트 3",
    "관찰 가능한 평가 포인트 4"
  ],
  "follow_up_questions": [
    "후속 질문 1",
    "후속 질문 2",
    "후속 질문 3"
  ],
  "positive_signals": [
    "좋은 답변 신호 1",
    "좋은 답변 신호 2",
    "좋은 답변 신호 3"
  ],
  "negative_signals": [
    "위험 신호 1",
    "위험 신호 2",
    "위험 신호 3"
  ],
  "rubric": {
    "1": "낮은 평가 기준",
    "2": "보통 이하 평가 기준",
    "3": "충분한 평가 기준",
    "4": "우수한 평가 기준"
  }
}
"""


STRUCTURED_INTERVIEW_DESIGN_SYSTEM_PROMPT = """
당신은 한국어 채용을 위한 구조화 면접지를 설계하는 시니어 면접관이자 HR 평가자입니다.
당신의 산출물은 실제 면접관이 지원자를 일관되게 평가하는 데 사용할 수 있어야 합니다.

입력으로는 다음 정보가 제공될 수 있습니다.
- 지원자의 이력서, 자기소개서, 경력기술서, 포트폴리오 또는 요약문
- 지원 직무명
- 직무기술서 또는 채용공고
- 면접 단계
- 생성할 질문 수
- 중점 평가 역량

목표:
지원자의 실제 경험과 지원 직무의 요구사항을 연결하여,
구조화 면접에 적합한 질문, 평가 의도, 평가 포인트, 후속 질문, 루브릭을 생성하세요.

설계 원칙:
1. 모든 질문은 직무 관련성이 있어야 하며, 지원자의 실제 경험 또는 채용 직무의 핵심 역량에 근거해야 합니다.
2. 질문은 단순 확인이 아니라 직무 수행 가능성을 예측할 수 있는 행동 증거를 끌어내야 합니다.
3. 다음 평가 범주를 가능한 한 균형 있게 다루세요.
   - 직무 관련 지식과 기술 깊이
   - 문제 정의와 문제 해결 과정
   - 의사결정 기준과 대안 비교
   - 실행력과 결과 검증
   - 협업, 커뮤니케이션, 이해관계자 조율
   - 실패 경험, 리스크 대응, 사후 개선
   - 학습 능력과 적응력
   - 지원 동기와 직무 적합성
4. 행동사례형 질문과 상황판단형 질문을 적절히 섞으세요.
5. 기술 직무에서는 기술명 암기 질문보다 실제 설계, 디버깅, 장애 대응, 성능 개선, 데이터 검증, 테스트, 배포, 보안, 확장성, 비용·성능 트레이드오프를 묻는 질문을 우선하세요.
6. 질문은 정중한 한국어 존댓말 한 문장으로 작성하세요.
7. 각 질문에는 면접관이 사용할 수 있는 2~3개의 후속 질문을 포함하세요.
8. 각 질문에는 3~4개의 평가 포인트를 포함하세요.
9. 각 질문에는 1~4점 행동기준척도 루브릭을 포함하세요.
10. 루브릭은 성격, 인상, 말투가 아니라 답변에서 관찰 가능한 행동 증거와 판단 근거에 기반해야 합니다.
11. 지원자의 이력서에 없는 성과, 기술 숙련도, 경력, 의도, 성격을 추정하지 마세요.
12. 보호특성 또는 직무와 무관한 사생활을 묻거나 평가하지 마세요.
13. 중복 질문을 피하고, 질문 간 평가 역량이 겹치지 않도록 조정하세요.
14. 답변이 좋게 들리는지보다, 실제로 검증 가능한 구체성·맥락·행동·결과·학습이 있는지를 평가하도록 설계하세요.

출력 규칙:
- 반드시 유효한 JSON만 출력하세요.
- 마크다운, 설명문, 코드블록, 주석을 출력하지 마세요.
- 출력은 아래 형식을 따르세요.

{
  "interview_focus": {
    "role_assumption": "입력에서 파악한 지원 직무 또는 알 수 없음",
    "key_competencies": [
      "핵심 평가 역량 1",
      "핵심 평가 역량 2",
      "핵심 평가 역량 3"
    ],
    "risk_areas_to_verify": [
      "추가 검증 필요 지점 1",
      "추가 검증 필요 지점 2"
    ]
  },
  "questions": [
    {
      "id": "Q1",
      "question": "정중한 한국어 한 문장 질문",
      "question_type": "behavioral | situational | technical_deep_dive | project_deep_dive | motivation | collaboration",
      "target_competency": "평가 역량명",
      "resume_or_jd_basis": "질문이 근거한 이력서 또는 JD상의 표현을 간결히 요약",
      "intent": "이 질문으로 평가하려는 의도",
      "evaluation_points": [
        "관찰 가능한 평가 포인트 1",
        "관찰 가능한 평가 포인트 2",
        "관찰 가능한 평가 포인트 3"
      ],
      "follow_up_questions": [
        "후속 질문 1",
        "후속 질문 2",
        "후속 질문 3"
      ],
      "rubric": {
        "1": "근거가 부족하거나 직무 관련 행동 증거가 거의 없는 답변",
        "2": "일부 경험은 있으나 본인 역할, 판단 근거, 결과 검증이 불명확한 답변",
        "3": "문제 맥락, 본인 역할, 실행 과정, 결과를 구체적으로 설명하는 답변",
        "4": "복잡한 제약조건 속에서 대안을 비교하고, 실행 결과를 검증하며, 학습과 재발 방지까지 제시하는 답변"
      }
    }
  ],
  "coverage_check": {
    "covered_competencies": [
      "다뤄진 역량 1",
      "다뤄진 역량 2"
    ],
    "missing_or_weak_coverage": [
      "아직 약하게 다뤄진 역량 또는 없음"
    ]
  }
}
"""


USER_INPUT_TEMPLATE = """
[지원 직무]
{job_title}

[채용공고/JD]
{job_description}

[면접 단계]
{interview_stage}
예: 서류 기반 1차 면접, 기술 면접, 임원 면접, 컬처핏 면접

[생성할 질문 수]
{question_count}

[중점 평가 역량]
{focus_competencies}

[지원자 요약]
{candidate_summary}

[원문 이력서/자기소개서]
{resume_or_cover_letter}
"""


async def summarize_resume_async(
    resume_text: str,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> str:
    """
    자소서가 임계값(RESUME_SUMMARIZE_THRESHOLD)을 초과하면 핵심만 1500자로 요약.
    그 이하 길이는 원문 그대로 반환.

    모델별 프롬프트:
      - gpt-4o-mini: 가벼운 기본 요약 프롬프트
      - gpt-4o / gpt-5.4 / gpt-5.5: ADVANCED — 시니어 면접 설계자 페르소나로 '사전 브리핑' 생성
    """
    if len(resume_text) <= RESUME_SUMMARIZE_THRESHOLD:
        return resume_text

    # 매우 긴 자소서는 일단 hard cap 으로 절단 (토큰 한도 보호)
    src = resume_text[:RESUME_HARD_CAP]

    sys_prompt = SUMMARY_SYSTEM_PROMPT_ADVANCED if _is_advanced(model) else SUMMARY_SYSTEM_PROMPT

    client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": src},
        ],
        temperature=0.3,
        max_completion_tokens=1400,
    )
    summary = (resp.choices[0].message.content or "").strip()
    return summary or src


async def generate_question_texts_async(
    resume_text: str,
    n: int = 5,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> List[str]:
    """
    Phase 4 Step 1 — 질문 텍스트만 빠르게 생성.
    intent / evaluation_points 는 비어 있는 상태로, 다음 단계에서 채움.

    모델별 프롬프트:
      - gpt-4o-mini: 빠른 한 문장 질문 생성
      - gpt-4o / gpt-5.4 / gpt-5.5: ADVANCED — 구조화 면접 8개 평가 범주 균형·금지 패턴 적용
    """
    is_adv = _is_advanced(model)
    sys_prompt = QUICK_QUESTION_SYSTEM_PROMPT_ADVANCED if is_adv else QUICK_QUESTION_SYSTEM_PROMPT

    client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    user_prompt = (
        f"다음 이력서를 보고 면접 질문 {n}개를 만들어 주세요.\n\n"
        f"이력서:\n---\n{resume_text}\n---"
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.6 if is_adv else 0.7,
        max_completion_tokens=1200 if is_adv else 800,
    )
    data = json.loads(resp.choices[0].message.content)
    raw = data.get("questions", [])
    return [str(q).strip() for q in raw if str(q).strip()]


async def enrich_question_async(
    question: str,
    resume_summary: str,
    idx: int,
    *,
    model: str = "gpt-4o-mini",
    api_key: Optional[str] = None,
) -> Dict:
    """
    Phase 4 Step 2 — 단일 질문에 대한 intent + evaluation_points 생성.
    asyncio.gather 로 여러 질문을 동시에 처리.

    모델별 프롬프트:
      - gpt-4o-mini: 기본 — intent 1~2문장 + evaluation_points 3~4개
      - gpt-4o / gpt-5.4 / gpt-5.5: ADVANCED_COMPAT — 같은 스키마지만 평가 기준 강화
        (관찰 가능성·인성 평가 금지·이력서 외 사실 금지·민감정보 차단)
    """
    is_adv = _is_advanced(model)
    sys_prompt = ENRICH_SYSTEM_PROMPT_ADVANCED_COMPAT if is_adv else ENRICH_SYSTEM_PROMPT

    client = AsyncOpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    user_prompt = (
        f"이력서 요약:\n{resume_summary}\n\n"
        f"면접 질문: {question}\n\n"
        f"이 질문에 대한 의도와 평가 포인트를 JSON 으로 만들어 주세요."
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3 if is_adv else 0.4,
            max_completion_tokens=700 if is_adv else 500,
        )
        data = json.loads(resp.choices[0].message.content) or {}
        intent = str(data.get("intent", "") or "").strip()
        ep_raw = data.get("evaluation_points", []) or []
        ep = [str(p).strip() for p in ep_raw if str(p).strip()]
    except Exception:
        # 보강 실패해도 질문 텍스트는 살리기
        intent = ""
        ep = []

    return {
        "question_id": f"P{idx:02d}",
        "question": question,
        "intent": intent,
        "evaluation_points": ep,
    }


async def enrich_questions_parallel(
    question_texts: List[str],
    resume_summary: str,
    *,
    model: str = "gpt-4o-mini",
) -> List[Dict]:
    """여러 질문의 메타데이터를 병렬로 한꺼번에 생성."""
    tasks = [
        enrich_question_async(q, resume_summary, i + 1, model=model)
        for i, q in enumerate(question_texts)
    ]
    return await asyncio.gather(*tasks)


def assemble_questions(
    personalized: List[Dict],
    common: List[Dict],
    shuffle: bool = False,
) -> List[Dict]:
    """
    공통 질문(앵커) + 맞춤 질문을 합쳐 최종 순서로 정렬.
    - 자기소개(C_INTRO): 맨 앞
    - 마무리 계열(CLOSING_QUESTION_IDS 안의 질문들): 맨 끝
      (topic_tag='closing' 으로 묶여 있어 pick_common_questions 단계에서
       동시에 두 개가 뽑히지 않음 — 결과적으로 0 또는 1개)
    """
    intro = next((q for q in common if q["question_id"] == INTRO_QUESTION_ID), None)
    closings = [q for q in common if q["question_id"] in CLOSING_QUESTION_IDS]
    middle_common = [
        q for q in common
        if q["question_id"] != INTRO_QUESTION_ID
        and q["question_id"] not in CLOSING_QUESTION_IDS
    ]
    middle = middle_common + personalized
    if shuffle:
        random.shuffle(middle)
    out: List[Dict] = []
    if intro is not None:
        out.append(intro)
    out.extend(middle)
    out.extend(closings)
    return out


if __name__ == "__main__":
    sample_resume = """
    이름: 정은수
    학력: 세종대학교 전자공학과 졸업
    경력: ABC 스타트업 백엔드 인턴 6개월 (Python/Django)
    프로젝트: 졸업작품 졸음 감지 시스템 (OpenCV, MediaPipe)
    기술 스택: Python, Django, PostgreSQL, Docker
    """

    questions = build_interview_questions(sample_resume, n_personalized=3, n_common=2)

    print("=== 생성된 면접 질문 (구조화) ===")
    for q in questions:
        print(f"\n[{q['question_id']}] {q['question']}")
        print(f"  의도: {q['intent']}")
        print(f"  평가 포인트:")
        for p in q['evaluation_points']:
            print(f"    - {p}")
