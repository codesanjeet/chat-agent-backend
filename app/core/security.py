"""Everything security-related lives here: JWT issuing/verification, password
hashing (unused today, ready if ever needed), and email-OTP helpers.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import PyJWTError
# from passlib.context import CryptContext

from .config import settings

# ---------------------------------------------------------------------------
# Password hashing -- NOT used anywhere in the current flow (Google + OTP
# only, no password_hash column on UserDB). Kept ready in case a future need
# (e.g. an enterprise client insisting on password login) comes up, so adding
# it later is "wire up two routes" instead of "add a new dependency and design
# a hashing scheme under time pressure." Argon2 is the current OWASP-recommended
# default for new systems (stronger against GPU cracking than bcrypt).
# ---------------------------------------------------------------------------
# pwd_context = CryptContext(
#     schemes=["argon2"],
#     deprecated="auto",
#     argon2__memory_cost=65536,   # 64 MB
#     argon2__time_cost=3,
#     argon2__parallelism=2,
# )


# def hash_password(password: str) -> str:
#     return pwd_context.hash(password)


# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT
#
# Access token: short-lived (30 min default), stateless -- verified locally on
# every request, never touches the DB. If stolen, it's only useful until it
# naturally expires; there is no way to revoke it early (accepted tradeoff for
# performance -- most systems make this tradeoff).
#
# Refresh token: long-lived (30 days default), carries a "jti" (unique ID) that
# is stored in the user_sessions table. Every refresh call checks that row is
# not revoked -- this is the "blacklist on logout" mechanism. Logout simply
# flips revoked=True on that row.
# ---------------------------------------------------------------------------
def create_access_token(data: dict[str, Any]) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc), "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc), "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Returns the decoded payload if the signature and expiry are valid, else
    None. Caller is responsible for checking payload["type"] matches what it
    expects (access vs refresh) -- this function only proves authenticity."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Email OTP (passwordless login)
# ---------------------------------------------------------------------------
OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_ATTEMPTS = 5


def generate_otp() -> str:
    """Cryptographically random digits (secrets, not random) -- avoids
    predictable sequences an attacker could exploit."""
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def hash_otp(code: str, email: str) -> str:
    """Salted with the email so identical codes for two different emails hash
    differently. A fast hash (sha256) is fine here -- unlike a password, this
    secret is single-use and dead in 10 minutes regardless."""
    return hashlib.sha256(f"{email.lower()}:{code}".encode()).hexdigest()


def otp_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)


def is_expired(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires_at










# google 
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings


class GoogleTokenError(Exception):
    pass


def verify_google_id_token(token: str) -> dict:
    """Verifies a Google id_token (sent from the Next.js frontend after Google Sign-In)
    and returns the decoded payload: {sub, email, name, ...}.

    Raises GoogleTokenError if the token is invalid, expired, or issued for a
    different client_id (protects against token substitution).
    """
    try:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as exc:
        raise GoogleTokenError(str(exc)) from exc

    if payload.get("aud") != settings.GOOGLE_CLIENT_ID:
        raise GoogleTokenError("Token audience mismatch")

    return payload
