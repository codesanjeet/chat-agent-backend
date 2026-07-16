import asyncio

import resend

from app.core.config import settings

resend.api_key = settings.RESEND_API_KEY


async def _send(to: str, subject: str, html: str) -> None:
    """Dev mode (no RESEND_API_KEY set): prints instead of sending, so the
    whole login flow is testable locally with zero email setup. Set
    RESEND_API_KEY in .env to send for real.

    resend's SDK is synchronous (blocking network I/O). Run it in a thread via
    asyncio.to_thread so one slow email send can't stall every other request
    this worker is handling.
    """
    if not settings.RESEND_API_KEY:
        print(f"[DEV EMAIL] to={to} subject={subject}\n{html}\n")
        return

    await asyncio.to_thread(
        resend.Emails.send,
        {
            "from": settings.EMAIL_FROM_ADDRESS,
            "to": [to],
            "subject": subject,
            "html": html,
        },
    )


async def send_otp_email(to: str, code: str) -> None:
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <h2 style="color:#0f172a;">Your login code</h2>
      <p style="color:#475569;">Use this code to log in. It expires in 10 minutes.</p>
      <div style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#2563eb;
                  text-align:center;padding:20px;background:#f1f5f9;border-radius:8px;">
        {code}
      </div>
      <p style="color:#94a3b8;font-size:12px;">
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """
    await _send(to, "Your login code", html)




async def send_welcome_email(to: str) -> None:
    html = """
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;">
      <h2 style="color:#0B0F19;">Welcome!</h2>
      <p style="color:#475569;">
        Thanks for signing up — your account is ready to go.
      </p>
      <p style="color:#475569;">
        Need help anytime? Our AI chat support is available 24x7 —
        <a href="https://agent.kiteum.com/chat/kiteum" style="color:#2B4EFF;">chat with us here</a>.
      </p>
      <p style="color:#94a3b8;font-size:12px;">
        This is an automated message. If you didn't create this account, you can ignore it.
      </p>
    </div>
    """
    await _send(to, "Welcome to Kiteum Agent", html)