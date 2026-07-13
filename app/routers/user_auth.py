import uuid
from datetime import datetime, timedelta, timezone
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.deps import get_current_user
from app.core.config import settings
from app.core.security import GoogleTokenError, verify_google_id_token
from app import rate_limiter
from app.core.security import (
    OTP_MAX_ATTEMPTS,
    OTP_RESEND_COOLDOWN_SECONDS,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
    hash_otp,
    is_expired,
)
from ..crud.user_auth import (
    consume_otp,
    create_otp,
    create_session,
    get_latest_active_otp,
    get_or_create_user_by_email,
    get_user_by_id,
    get_valid_session,
    register_failed_otp_attempt,
    revoke_session,
    touch_last_login,
    touch_session,
)
from ..core.database import get_db
from app.models.user import UserDB
from app.schemas.user_auth import GoogleLoginRequest, OtpRequest, OtpVerify, TokenResponse, UserOut
from app.services.email import send_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * settings.REFRESH_TOKEN_EXPIRE_DAYS


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _issue_tokens(db: AsyncSession, response: Response, user: UserDB, request: Request) -> TokenResponse:
    """Shared by every login method (OTP, Google, refresh): mint an access
    token, mint + persist a refresh token (as a session row so it's revocable),
    set the refresh token as an httpOnly cookie, and return the access token
    in the body for the frontend to hold in memory."""
    access_token = create_access_token({"sub": str(user.id)})

    jti = str(uuid.uuid4())
    refresh_token = create_refresh_token({"sub": str(user.id), "jti": jti})
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    await create_session(
        db,
        user_id=user.id,
        jti=jti,
        expires_at=expires_at,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    await touch_last_login(db, user)

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,          # frontend JS can never read this -- mitigates XSS token theft
        secure=True,             # requires HTTPS; only set False for local http:// dev
        samesite="lax",
        max_age=REFRESH_COOKIE_MAX_AGE,
        path="/auth",            # only sent back to auth endpoints, not every request
    )

    return TokenResponse(access_token=access_token, user=UserOut.model_validate(user))


@router.post("/otp/request", status_code=status.HTTP_204_NO_CONTENT)
async def request_otp(payload: OtpRequest, request: Request, db: AsyncSession = Depends(get_db)):
    email = payload.email.lower().strip()
    ip = _client_ip(request)

    # Two independent limits: per-IP (stop one client from hammering many
    # different emails) and per-email (stop repeated codes to one inbox -- also
    # reinforced below by the resend cooldown against the last issued code).
    if not await rate_limiter.check(f"otp_request_ip:{ip}", max_attempts=10, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")
    if not await rate_limiter.check(f"otp_request_email:{email}", max_attempts=5, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests for this email, try again later")

    last_otp = await get_latest_active_otp(db, email)
    if last_otp and not is_expired(last_otp.expires_at):
        seconds_since = (datetime.now(timezone.utc) - last_otp.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        if seconds_since < OTP_RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Please wait {int(OTP_RESEND_COOLDOWN_SECONDS - seconds_since)}s before requesting another code",
            )

    code = generate_otp()
    await create_otp(db, email=email, code_hash=hash_otp(code, email))
    await send_otp_email(email, code)
    return None


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_otp(payload: OtpVerify, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """The ONLY place a user gets created via the OTP path -- guaranteeing
    every account has a verified email before it exists."""
    email = payload.email.lower().strip()
    ip = _client_ip(request)

    if not await rate_limiter.check(f"otp_verify_ip:{ip}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again later")

    otp = await get_latest_active_otp(db, email)
    if otp is None or is_expired(otp.expires_at):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code expired or not found, request a new one")
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Too many incorrect attempts, request a new code")

    if not hmac.compare_digest(otp.code_hash, hash_otp(payload.code, email)):
        await register_failed_otp_attempt(db, otp)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect code")

    await consume_otp(db, otp)
    user = await get_or_create_user_by_email(db, email=email)
    return await _issue_tokens(db, response, user, request)


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)
):
    ip = _client_ip(request)
    if not await rate_limiter.check(f"google_login_ip:{ip}", max_attempts=20, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, try again later")

    try:
        google_payload = verify_google_id_token(payload.id_token)
    except GoogleTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")

    email = google_payload.get("email")
    if not email or not google_payload.get("email_verified", False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Google account has no verified email")

    # Find-or-create BY EMAIL (not google_id) -- so an account created via OTP
    # first gets linked here instead of creating a duplicate.
    user = await get_or_create_user_by_email(
        db,
        email=email,
        google_id=google_payload["sub"],
    )
    return await _issue_tokens(db, response, user, request)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_access_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Rotates the refresh token on every use: the old jti is revoked and a new
    one issued. If a stolen refresh token is ever used by an attacker, the
    legitimate user's next refresh attempt will fail (their token was already
    consumed) -- a clear signal that account was compromised."""
    ip = _client_ip(request)
    if not await rate_limiter.check(f"refresh_ip:{ip}", max_attempts=30, window_seconds=600):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later")

    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")

    payload = decode_token(refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    jti = payload.get("jti")
    session = await get_valid_session(db, jti)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or revoked, please log in again")

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or user.is_blocked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer valid")

    await touch_session(db, session)
    await revoke_session(db, jti)  # rotation: this refresh token is now single-use

    return await _issue_tokens(db, response, user, request)


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Revokes (blacklists) the session behind this refresh token. Any access
    token already issued stays valid until it naturally expires within
    ACCESS_TOKEN_EXPIRE_MINUTES -- that short window is the accepted tradeoff
    for not hitting the DB on every single request."""
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh_token:
        payload = decode_token(refresh_token)
        if payload and payload.get("jti"):
            await revoke_session(db, payload["jti"])

    response.delete_cookie(REFRESH_COOKIE_NAME, path="/auth")
    return {"status": "success", "detail": "Logged out"}


@router.get("/me", response_model=UserOut)
async def get_me(user: UserDB = Depends(get_current_user)):
    return UserOut.model_validate(user)