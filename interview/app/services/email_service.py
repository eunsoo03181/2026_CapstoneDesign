"""
이메일 발송 — Gmail SMTP 기반.

[설정 절차 — Gmail 앱 비밀번호 1회 발급]
1. Google 계정에 2단계 인증(2FA) 활성화 — 필수
   https://myaccount.google.com/security
2. 같은 페이지에서 '앱 비밀번호' 클릭
   https://myaccount.google.com/apppasswords
3. 앱 이름: "Signal Catch" (자유) → 만들기
4. 16자리 비밀번호 표시됨 (예: abcd efgh ijkl mnop) — 한 번만 보이니 즉시 복사
5. .env 에 다음 두 줄 추가:
     SMTP_USER=your-gmail@gmail.com
     SMTP_APP_PASSWORD=abcdefghijklmnop      # 공백 제거 후 16자 그대로

설정 안 됐을 때 동작
  - 환경변수 비어있으면 메일 발송 시도 안 함.
  - 대신 콘솔(uvicorn 로그)에 인증 링크 출력 → 개발/시연 시 사용자가 직접 클릭.
"""

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT_SSL = 465


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_USER") and os.getenv("SMTP_APP_PASSWORD"))


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
) -> bool:
    """Gmail SMTP 로 이메일 1통 발송.

    반환: True 발송 성공, False 실패(또는 미설정).
    실패해도 예외 던지지 않음 (signup 흐름 보호).
    """
    sender = os.getenv("SMTP_USER", "").strip()
    app_pw = os.getenv("SMTP_APP_PASSWORD", "").strip()
    from_name = os.getenv("SMTP_FROM_NAME", "Signal Catch")

    if not (sender and app_pw):
        # 설정 안 됨 — 콘솔 fallback
        log.warning(
            "[email] SMTP 미설정 — 메일 발송 skip. "
            "to=%s, subject=%r\n--- 본문 (text) ---\n%s",
            to_email, subject, text_body or "(html only)",
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{sender}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT_SSL, context=ctx, timeout=10) as smtp:
            smtp.login(sender, app_pw)
            smtp.sendmail(sender, [to_email], msg.as_string())
        log.info("[email] sent to=%s subject=%r", to_email, subject)
        return True
    except Exception as e:
        log.error("[email] send failed to=%s: %s", to_email, e)
        return False


def send_verification_email(
    to_email: str,
    user_name: str,
    verify_url: str,
) -> bool:
    """회원가입 직후 인증 메일 발송."""
    subject = "[Signal Catch] 이메일 인증을 완료해주세요"
    text_body = (
        f"안녕하세요 {user_name}님,\n\n"
        f"Signal Catch 회원가입을 완료하려면 아래 링크를 클릭해 이메일을 인증해주세요.\n"
        f"링크는 24시간 동안만 유효합니다.\n\n"
        f"{verify_url}\n\n"
        f"본인이 가입을 신청하지 않았다면 이 메일을 무시하셔도 됩니다.\n"
    )
    html_body = f"""<!DOCTYPE html>
<html><body style="font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 480px; margin: 32px auto; color:#0f172a;">
  <div style="text-align:center;margin-bottom:24px;">
    <div style="display:inline-block;width:48px;height:48px;border-radius:12px;background:#4f46e5;line-height:48px;color:white;font-size:20px;">⚡</div>
    <h1 style="font-size:20px;margin:12px 0 4px;">Signal Catch</h1>
    <p style="color:#64748b;font-size:13px;margin:0;">AI 모의면접 시뮬레이터</p>
  </div>

  <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:28px;">
    <p style="font-size:15px;margin:0 0 12px;"><b>{user_name}</b>님, 가입을 환영해요 👋</p>
    <p style="font-size:14px;color:#475569;line-height:1.6;margin:0 0 20px;">
      회원가입을 완료하려면 아래 버튼을 눌러 이메일을 인증해주세요.<br>
      이 링크는 <b>24시간</b> 동안만 유효합니다.
    </p>
    <p style="text-align:center;margin:24px 0;">
      <a href="{verify_url}" style="display:inline-block;background:#4f46e5;color:white;text-decoration:none;padding:12px 28px;border-radius:8px;font-weight:600;font-size:14px;">
        이메일 인증하기
      </a>
    </p>
    <p style="font-size:12px;color:#94a3b8;line-height:1.5;margin:20px 0 0;">
      버튼이 동작하지 않으면 아래 링크를 복사해 브라우저에 붙여넣으세요:<br>
      <span style="word-break:break-all;color:#475569;">{verify_url}</span>
    </p>
  </div>

  <p style="font-size:11px;color:#94a3b8;text-align:center;margin:20px 0 0;">
    본인이 가입을 신청하지 않았다면 이 메일을 무시하셔도 됩니다.<br>
    © 2026 Signal Catch
  </p>
</body></html>"""
    return send_email(to_email, subject, html_body, text_body)


def smtp_status() -> dict:
    """관리자/디버그용 — 현재 SMTP 설정 여부 + sender 정보."""
    return {
        "configured": _smtp_configured(),
        "sender": os.getenv("SMTP_USER", "") if _smtp_configured() else None,
        "from_name": os.getenv("SMTP_FROM_NAME", "Signal Catch"),
    }
