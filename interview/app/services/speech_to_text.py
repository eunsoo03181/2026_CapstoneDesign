"""
사용자 음성 답변을 텍스트로 변환.

[현재 활성] OpenAI Whisper API (whisper-1)
  - 환경변수: OPENAI_API_KEY 만 있으면 동작
  - 파일 크기 제한 25MB (16kHz mono wav 기준 약 25분)
  - 한국어 인식 정확도 양호, 길이 여유, 키 발급 1회로 끝

[보존(주석)] CLOVA CSR / CLOVA Speech 장문 인식
  - 추후 한국어 정확도 우선 또는 NCP 환경 통합 시 재활성화
  - 함수 본체와 import는 아래 주석 블록 참고
"""

import os
import sys
import time
import wave
import select
import re
from typing import Any, Dict, Optional


# ============================================================
# Whisper 환각(hallucination) 필터
# ============================================================
# 무음/소음 구간에서 Whisper 가 자주 만들어내는 패턴들 — YouTube 자막 학습 데이터
# 흔적. 답변이 이 패턴 중 하나로만 구성돼 있으면 무효(공백) 처리.
WHISPER_KO_HALLUCINATIONS = [
    "시청해주셔서 감사합니다",
    "시청해 주셔서 감사합니다",
    "시청해주셔서감사합니다",
    "구독과 좋아요 부탁드립니다",
    "구독 좋아요 부탁드립니다",
    "구독, 좋아요 부탁드립니다",
    "구독과 좋아요 부탁드려요",
    "감사합니다",
    "고맙습니다",
    "다음 영상에서 만나요",
    "다음 영상에서 뵙겠습니다",
    "다음 시간에 만나요",
    "오늘도 좋은 하루 보내세요",
    "MBC 뉴스",
    "MBC 뉴스 박혜진입니다",
    "한국어 자막",
    "자막 제공",
    "이상 mbc뉴스였습니다",
]


def _normalize_for_match(s: str) -> str:
    """비교용 정규화 — 공백·구두점 제거, 소문자."""
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[\s\.,!?\-~·…\(\)\[\]\"'’‘]", "", s)
    return s


_HALLUCINATION_NORMALIZED = {
    _normalize_for_match(p) for p in WHISPER_KO_HALLUCINATIONS
}


def is_whisper_hallucination(text: str) -> bool:
    """전체 답변이 알려진 환각 패턴 중 하나면 True.

    조건: 정규화 후 매칭. 길이 짧고 (예: 한 줄 정도) 환각 패턴이 단독으로 들어간 경우.
    사용자가 진짜 답변 안에 '감사합니다' 한 문장만 했을 가능성이 있어도, 면접 답변으로는
    의미 없는 한 문장이라 0점 처리하는 게 안전.
    """
    norm = _normalize_for_match(text or "")
    if not norm:
        return True   # 빈 문자열도 hallucination 으로 취급
    return norm in _HALLUCINATION_NORMALIZED


def clean_transcript(text: str) -> str:
    """STT 결과 후처리 — 환각이면 빈 문자열 반환, 아니면 strip 만."""
    text = (text or "").strip()
    if is_whisper_hallucination(text):
        return ""
    return text

import numpy as np
import sounddevice as sd

# CLOVA 사용 시 아래 import 도 함께 주석 해제
# import json
# import requests


SAMPLE_RATE = 16000     # Whisper / CLOVA 모두 16kHz 권장
CHANNELS = 1            # mono


def record_audio(filename: str, seconds: float = 30.0) -> str:
    """[고정 길이] 마이크에서 seconds 초간 녹음. 디버깅용."""
    print(f"녹음 시작 ({seconds}초)...")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
    )
    sd.wait()
    print("녹음 완료")

    with wave.open(filename, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    return filename


def _stdin_has_input() -> bool:
    """stdin 에 한 줄 입력이 도착했는지 비차단으로 확인 (Unix 전용)."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            sys.stdin.readline()  # 버퍼 비우기
            return True
    except (OSError, ValueError):
        pass
    return False


def record_audio_interactive(
    filename: str,
    max_seconds: float = 180.0,
) -> str:
    """
    조기 종료 가능한 녹음. 다음 중 하나 만족 시 종료:
      1) max_seconds 경과
      2) 사용자가 Enter 키를 다시 누름

    (참고) 무음 기반 자동 종료는 임계값 튜닝이 까다로워 제거.
           "답변이 끝났는지" 판단은 추후 카메라 분석(얼굴/입술)과 종합해서 처리 예정.
    """
    print(f"녹음 시작 (최대 {max_seconds:.0f}초). Enter 다시 누르면 즉시 종료.")

    chunks = []
    start = time.time()
    stop_reason = "max"

    def callback(indata, frames, time_info, status):
        if status:
            pass  # 오버플로 등 경고 무시
        chunks.append(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=callback,
        blocksize=int(SAMPLE_RATE * 0.1),  # 100ms 청크
    ):
        while True:
            time.sleep(0.1)
            elapsed = time.time() - start

            if elapsed >= max_seconds:
                stop_reason = "max"
                break
            if _stdin_has_input():
                stop_reason = "enter"
                break

    msg = {
        "max":   f"종료 (최대 {max_seconds:.0f}초 경과)",
        "enter": "종료 (Enter 입력)",
    }[stop_reason]
    print(f"  {msg}, 길이 {time.time() - start:.1f}초")

    # wav 저장
    if chunks:
        audio = np.concatenate(chunks, axis=0)
    else:
        audio = np.zeros((0, CHANNELS), dtype="int16")
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    return filename


# ============================================================
# 활성 백엔드: OpenAI Whisper
# ============================================================
def transcribe_with_whisper(
    audio_path: str,
    model: str = "whisper-1",
    language: str = "ko",
    api_key: Optional[str] = None,
) -> str:
    """
    OpenAI Whisper API로 음성 → 텍스트 변환 (텍스트만 반환, 옛 호환용).
    환경변수: OPENAI_API_KEY
    파일 크기 제한: 25MB (16kHz mono wav 기준 약 25분 이내).
    """
    res = transcribe_with_whisper_words(
        audio_path, model=model, language=language, api_key=api_key,
    )
    return res.get("text", "") if isinstance(res, dict) else str(res)


def transcribe_with_whisper_words(
    audio_path: str,
    model: str = "whisper-1",
    language: str = "ko",
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Whisper STT + 단어별 timestamp.
    음성 비언어 분석(말 속도/침묵/반복)을 위해 word-level timestamp 필요.
    반환: {"text": "...", "words": [{"word": "...", "start": 0.12, "end": 0.34}, ...]}

    timestamp_granularities=["word"] 옵션은 verbose_json 응답에서 단어별 timestamp 를 받음.
    네트워크 일시 오류 시엔 text 만이라도 받아 빈 words 와 함께 반환.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    try:
        with open(audio_path, "rb") as f:
            res = client.audio.transcriptions.create(
                model=model,
                file=f,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        # res 는 pydantic 모델 또는 dict 형태
        if hasattr(res, "model_dump"):
            data = res.model_dump()
        elif isinstance(res, dict):
            data = res
        else:
            data = {"text": getattr(res, "text", str(res)), "words": []}
        # Whisper 환각 필터 — 무음 구간에서 'YouTube 자막' 같은 환각 패턴이 자주 나옴
        text = clean_transcript(data.get("text", "") or "")
        words = data.get("words", []) or []
        # 텍스트가 환각으로 비워졌으면 words 도 의미 없음 → 비움
        if not text:
            words = []
        return {"text": text, "words": words}
    except Exception:
        # 폴백: text-only 호출
        try:
            with open(audio_path, "rb") as f:
                res = client.audio.transcriptions.create(
                    model=model, file=f, language=language,
                    response_format="text",
                )
            text = res if isinstance(res, str) else getattr(res, "text", str(res))
        except Exception:
            text = ""
        # 폴백 경로에서도 환각 필터 적용
        text = clean_transcript(text)
        return {"text": text, "words": []}


# ============================================================
# (보존) CLOVA 백엔드 — 현재 비활성
# 사용 시: 아래 두 함수 + 상단 `import json`, `import requests` 주석 해제,
#         capture_user_answer() 의 분기 주석 해제, 환경변수 등록.
# ============================================================
# def transcribe_with_clova_csr(
#     audio_path: str,
#     client_id: Optional[str] = None,
#     client_secret: Optional[str] = None,
#     lang: str = "Kor",
# ) -> str:
#     """
#     Naver CLOUD CSR(짧은 음성, ~60초) API로 음성→텍스트 변환.
#     환경변수: NCP_CLIENT_ID, NCP_CLIENT_SECRET
#     """
#     client_id = client_id or os.getenv("NCP_CLIENT_ID")
#     client_secret = client_secret or os.getenv("NCP_CLIENT_SECRET")
#     if not client_id or not client_secret:
#         raise RuntimeError("NCP_CLIENT_ID / NCP_CLIENT_SECRET 환경변수를 설정하세요.")
#
#     url = f"https://naveropenapi.apigw.ntruss.com/recog/v1/stt?lang={lang}"
#     headers = {
#         "X-NCP-APIGW-API-KEY-ID": client_id,
#         "X-NCP-APIGW-API-KEY": client_secret,
#         "Content-Type": "application/octet-stream",
#     }
#     with open(audio_path, "rb") as f:
#         data = f.read()
#
#     res = requests.post(url, headers=headers, data=data, timeout=60)
#     res.raise_for_status()
#     return res.json().get("text", "")
#
#
# def transcribe_with_clova_long(
#     audio_path: str,
#     invoke_url: Optional[str] = None,
#     secret_key: Optional[str] = None,
#     language: str = "ko-KR",
# ) -> str:
#     """
#     CLOVA Speech 장문 인식 API. 면접 답변(1~3분)에 적합.
#     환경변수: CLOVA_INVOKE_URL, CLOVA_SECRET_KEY
#     """
#     invoke_url = invoke_url or os.getenv("CLOVA_INVOKE_URL")
#     secret_key = secret_key or os.getenv("CLOVA_SECRET_KEY")
#     if not invoke_url or not secret_key:
#         raise RuntimeError("CLOVA_INVOKE_URL / CLOVA_SECRET_KEY 환경변수를 설정하세요.")
#
#     url = f"{invoke_url.rstrip('/')}/recognizer/upload"
#     params = {"language": language, "completion": "sync"}
#     headers = {"X-CLOVASPEECH-API-KEY": secret_key}
#
#     with open(audio_path, "rb") as f:
#         files = {
#             "media": f,
#             "params": (None, json.dumps(params), "application/json"),
#         }
#         res = requests.post(url, headers=headers, files=files, timeout=180)
#     res.raise_for_status()
#
#     data = res.json()
#     return data.get("text") or "".join(seg.get("text", "") for seg in data.get("segments", []))
# ============================================================


def capture_user_answer(
    save_path: str = "answer.wav",
    seconds: float = 180.0,
    backend: str = "whisper",
    interactive: bool = True,
) -> str:
    """
    사용자 음성 녹음 → STT → 텍스트 반환.

    interactive=True (기본):
      - 최대 seconds 초 녹음
      - Enter 다시 누르면 즉시 종료
      ※ 무음 기반 자동 종료는 제거. 추후 카메라 분석과 종합해 별도 판단 예정.

    interactive=False:
      - 정확히 seconds 초간 녹음 (디버깅용)

    backend:
      - "whisper"     : OpenAI Whisper (현재 활성, 기본값)
      - "clova_csr"   : 비활성. 사용 시 위 CLOVA 블록 주석 해제 필요
      - "clova_long"  : 비활성. 사용 시 위 CLOVA 블록 주석 해제 필요
    """
    if interactive:
        record_audio_interactive(save_path, max_seconds=seconds)
    else:
        record_audio(save_path, seconds=seconds)

    if backend == "whisper":
        return transcribe_with_whisper(save_path)

    # ---- CLOVA 재활성화 시 아래 주석 해제 ----
    # if backend == "clova_csr":
    #     return transcribe_with_clova_csr(save_path)
    # if backend == "clova_long":
    #     return transcribe_with_clova_long(save_path)

    raise ValueError(
        f"backend='{backend}' 는 현재 비활성. "
        f"speech_to_text.py 의 CLOVA 주석 블록을 해제한 뒤 사용하세요."
    )


if __name__ == "__main__":
    # 짧게 한 번 테스트
    answer_text = capture_user_answer(save_path="answer.wav", seconds=10.0)
    print("=== 변환된 사용자 답변 ===")
    print(answer_text)
