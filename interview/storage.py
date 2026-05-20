"""
파일 저장소 추상화 — 영상은 Supabase Storage, 그 외(음성/이력서)는 로컬.

URI 스키마로 위치를 명시:
  - "local:sessions/{sid}/answer_1.webm"
  - "supabase:interview-video/{sid}/video_1.webm"

조회 시 prefix 보고 자동 분기 (양쪽 조회 불필요).

환경변수:
  SUPABASE_URL          : https://[ref].supabase.co
  SUPABASE_SERVICE_KEY  : Service role 키 (서버 전용)
"""

import os
import threading
from pathlib import Path
from typing import Optional, BinaryIO


# Supabase 클라이언트는 첫 사용 시 1회 초기화 (싱글톤)
_supabase_client = None
_supabase_lock = threading.Lock()


def _get_supabase_client():
    global _supabase_client
    if _supabase_client is None:
        with _supabase_lock:
            if _supabase_client is None:
                from supabase import create_client
                url = os.getenv("SUPABASE_URL")
                key = os.getenv("SUPABASE_SERVICE_KEY")
                if not url or not key:
                    raise RuntimeError(
                        "SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 필요합니다."
                    )
                _supabase_client = create_client(url, key)
    return _supabase_client


# ============================================================
# 영상 업로드 (Supabase Storage)
# ============================================================

VIDEO_BUCKET = "interview-video"


def upload_video(
    file_bytes: bytes,
    session_code: str,
    q_idx: int,
    content_type: str = "video/webm",
    extension: str = "webm",
) -> str:
    """
    영상 파일을 Supabase Storage 의 interview-video 버킷에 업로드.

    저장 경로: {session_code}/answer_{q_idx}.{extension}
    반환: URI 스키마 → "supabase:interview-video/{session_code}/answer_{q_idx}.{ext}"

    같은 경로가 있으면 덮어쓰기.
    """
    client = _get_supabase_client()
    path = f"{session_code}/answer_{q_idx}.{extension}"

    # upsert (덮어쓰기 허용)
    res = client.storage.from_(VIDEO_BUCKET).upload(
        path=path,
        file=file_bytes,
        file_options={
            "content-type": content_type,
            "upsert": "true",
        },
    )
    # storage3 응답 객체 — 실패 시 raise 됨
    return f"supabase:{VIDEO_BUCKET}/{path}"


def get_video_signed_url(uri: str, expires_in: int = 3600) -> str:
    """
    저장된 영상의 다운로드 가능한 서명 URL 발급 (기본 1시간 유효).

    uri 형태:
      "supabase:interview-video/{sid}/answer_1.webm"
    """
    if not uri.startswith("supabase:"):
        raise ValueError(f"Supabase URI 아님: {uri[:30]}...")
    bucket_and_path = uri.removeprefix("supabase:")
    bucket, _, path = bucket_and_path.partition("/")
    client = _get_supabase_client()
    res = client.storage.from_(bucket).create_signed_url(path, expires_in)
    # 응답 형태: {"signedURL": "..."} 또는 {"signedUrl": "..."} (버전 따라)
    return res.get("signedURL") or res.get("signedUrl") or ""


def delete_video(uri: str) -> bool:
    """저장된 영상 삭제. 성공 시 True."""
    if not uri.startswith("supabase:"):
        return False
    bucket_and_path = uri.removeprefix("supabase:")
    bucket, _, path = bucket_and_path.partition("/")
    try:
        client = _get_supabase_client()
        client.storage.from_(bucket).remove([path])
        return True
    except Exception:
        return False


# ============================================================
# 음성 / 일반 파일 — 로컬 경로 헬퍼 (기존 호환)
# ============================================================

def local_uri(path: str) -> str:
    """로컬 경로를 URI 스키마로 표준화."""
    return f"local:{path}"


def parse_uri(uri: str):
    """
    URI 분해: (scheme, location)
      "local:sessions/abc/x.wav"          → ("local", "sessions/abc/x.wav")
      "supabase:interview-video/abc/x"    → ("supabase", "interview-video/abc/x")
      "sessions/abc/x.wav" (prefix 없음)  → ("local", "sessions/abc/x.wav")  (호환)
    """
    if not uri:
        return (None, "")
    if uri.startswith("local:"):
        return ("local", uri[6:])
    if uri.startswith("supabase:"):
        return ("supabase", uri[9:])
    # prefix 없는 옛 데이터는 local 로 간주 (마이그레이션 호환)
    return ("local", uri)


def resolve_for_download(uri: str, expires_in: int = 3600) -> Optional[str]:
    """
    URI 를 실제 다운로드 가능한 형태로 변환.
      - local:    → 그대로 (로컬 파일 경로 반환, FastAPI 가 FileResponse 로 서빙)
      - supabase: → signed URL 발급해서 반환
    """
    scheme, loc = parse_uri(uri)
    if scheme == "supabase":
        return get_video_signed_url(uri, expires_in)
    if scheme == "local":
        return loc  # 로컬 경로 그대로
    return None
