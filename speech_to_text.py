"""
사용자 음성 답변을 Naver CLOVA Speech로 텍스트 변환.

- record_audio: 마이크에서 일정 시간 녹음하여 wav 파일 저장
- transcribe_with_clova_csr: 짧은 음성(약 60초 이하)을 CLOVA CSR로 변환
- transcribe_with_clova_long: 긴 음성을 CLOVA Speech 장문 인식 API로 변환
"""

import os
import wave
import json
import requests
from typing import Optional

import sounddevice as sd


SAMPLE_RATE = 16000     # CLOVA 권장 샘플레이트
CHANNELS = 1            # mono


def record_audio(filename: str, seconds: float = 30.0) -> str:
    """마이크에서 seconds 초간 녹음하여 wav 파일로 저장. 파일 경로 반환."""
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
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    return filename


def transcribe_with_clova_csr(
    audio_path: str,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    lang: str = "Kor",
) -> str:
    """
    Naver CLOUD CSR(짧은 음성, ~60초) API로 음성→텍스트 변환.
    환경변수: NCP_CLIENT_ID, NCP_CLIENT_SECRET
    """
    client_id = client_id or os.getenv("NCP_CLIENT_ID")
    client_secret = client_secret or os.getenv("NCP_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("NCP_CLIENT_ID / NCP_CLIENT_SECRET 환경변수를 설정하세요.")

    url = f"https://naveropenapi.apigw.ntruss.com/recog/v1/stt?lang={lang}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Content-Type": "application/octet-stream",
    }
    with open(audio_path, "rb") as f:
        data = f.read()

    res = requests.post(url, headers=headers, data=data, timeout=60)
    res.raise_for_status()
    return res.json().get("text", "")


def transcribe_with_clova_long(
    audio_path: str,
    invoke_url: Optional[str] = None,
    secret_key: Optional[str] = None,
    language: str = "ko-KR",
) -> str:
    """
    CLOVA Speech 장문 인식 API. 면접 답변(1~3분)에 적합.
    환경변수: CLOVA_INVOKE_URL, CLOVA_SECRET_KEY
    """
    invoke_url = invoke_url or os.getenv("CLOVA_INVOKE_URL")
    secret_key = secret_key or os.getenv("CLOVA_SECRET_KEY")
    if not invoke_url or not secret_key:
        raise RuntimeError("CLOVA_INVOKE_URL / CLOVA_SECRET_KEY 환경변수를 설정하세요.")

    url = f"{invoke_url.rstrip('/')}/recognizer/upload"
    params = {"language": language, "completion": "sync"}
    headers = {"X-CLOVASPEECH-API-KEY": secret_key}

    with open(audio_path, "rb") as f:
        files = {
            "media": f,
            "params": (None, json.dumps(params), "application/json"),
        }
        res = requests.post(url, headers=headers, files=files, timeout=180)
    res.raise_for_status()

    data = res.json()
    return data.get("text") or "".join(seg.get("text", "") for seg in data.get("segments", []))


def capture_user_answer(
    save_path: str = "answer.wav",
    seconds: float = 30.0,
    long_form: bool = False,
) -> str:
    """
    사용자 음성 녹음 → CLOVA로 변환 → 텍스트 답변 반환.
    long_form=True 면 장문 인식 API 사용.
    """
    record_audio(save_path, seconds=seconds)
    if long_form:
        return transcribe_with_clova_long(save_path)
    return transcribe_with_clova_csr(save_path)


if __name__ == "__main__":
    # 30초 녹음 후 텍스트로 변환
    answer_text = capture_user_answer(save_path="answer.wav", seconds=30.0)
    print("=== 변환된 사용자 답변 ===")
    print(answer_text)
