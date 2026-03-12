"""Mail gateway server for FisioManager.

Purpose
- Keep Gmail/SMTP credentials on a server (not inside the APK).
- The mobile/desktop app calls this service to send emails (optionally with a PDF attachment).

Run (example)
  export MAIL_GATEWAY_API_KEY='change-me'
  export MAIL_GMAIL_SENDER='fisiomanagerspain@gmail.com'
  export MAIL_GMAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx'
  python tools/mail_gateway_server.py

Then configure the app:
- Settings (admin) -> Mail gateway URL/key

Security note
- If MAIL_GATEWAY_API_KEY is empty, the gateway is open and can be abused.
"""

from __future__ import annotations

import base64
import os
import smtplib
from email.message import EmailMessage
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request


app = FastAPI(title="FisioManager Mail Gateway")


def _safe_str(x: Any) -> str:
    return str(x or "").strip()


def _require_api_key(x_api_key: str | None) -> None:
    required = _safe_str(os.getenv("MAIL_GATEWAY_API_KEY"))
    if not required:
        return
    if _safe_str(x_api_key) != required:
        raise HTTPException(status_code=401, detail="invalid api key")


def _send_via_gmail(*, to_email: str, subject: str, body: str, attachment: tuple[bytes, str] | None = None) -> None:
    sender = _safe_str(os.getenv("MAIL_GMAIL_SENDER")) or _safe_str(os.getenv("FISIOMANAGER_GMAIL_SENDER"))
    app_pw = _safe_str(os.getenv("MAIL_GMAIL_APP_PASSWORD")) or _safe_str(os.getenv("FISIOMANAGER_GMAIL_APP_PASSWORD"))
    if not sender or not app_pw:
        raise RuntimeError("gmail not configured")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = _safe_str(to_email)
    msg["Subject"] = _safe_str(subject)
    msg.set_content(_safe_str(body))

    if attachment is not None:
        raw, filename = attachment
        filename = _safe_str(filename) or "attachment.pdf"
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        msg.add_attachment(raw, maintype="application", subtype="pdf", filename=filename)

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(sender, app_pw)
        s.send_message(msg)


@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "true"}


@app.post("/send")
async def send(request: Request, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid payload")

    to_email = _safe_str(payload.get("to_email") or payload.get("to") or "")
    subject = _safe_str(payload.get("subject") or "")
    body = _safe_str(payload.get("body") or payload.get("text") or "")

    if "@" not in to_email or "." not in to_email:
        raise HTTPException(status_code=400, detail="invalid to_email")
    if not subject:
        subject = "FisioManager"

    attachment_b64 = _safe_str(payload.get("attachment_b64") or "")
    attachment_name = _safe_str(payload.get("attachment_name") or payload.get("filename") or "")
    attachment: tuple[bytes, str] | None = None

    if attachment_b64:
        try:
            raw = base64.b64decode(attachment_b64.encode("utf-8"), validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid attachment_b64")
        # Basic size guard (10MB)
        if len(raw) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="attachment too large")
        attachment = (raw, attachment_name or "document.pdf")

    try:
        _send_via_gmail(to_email=to_email, subject=subject, body=body, attachment=attachment)
        return {"ok": True, "message": "sent"}
    except Exception as ex:
        # Don't leak server secrets; return a concise message.
        return {"ok": False, "message": f"send failed: {_safe_str(ex)}"}


if __name__ == "__main__":
    import uvicorn

    host = _safe_str(os.getenv("HOST")) or "0.0.0.0"
    port_s = _safe_str(os.getenv("PORT")) or "8000"
    try:
        port = int(port_s)
    except Exception:
        port = 8000

    uvicorn.run(app, host=host, port=port)
