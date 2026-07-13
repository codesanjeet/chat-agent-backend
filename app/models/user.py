import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid, event, func,BigInteger,Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class UserDB(Base):
    """A business account. This IS the tenant for MVP -- one login = one
    business, no separate team/roles model yet. No password column: login is
    Google OAuth or email OTP only (see core/security.py for why)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_category: Mapped[str| None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    contact_number : Mapped[int] = mapped_column(Integer,nullable=True, index=True)

    # Nullable: a user who only ever used OTP has no Google identity yet.
    # Postgres allows multiple NULLs under a unique constraint, so this is safe.
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    google_linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    is_blocked: Mapped[bool] = mapped_column(default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Business config -- JSON so a business can add fields without a migration.
    social_links: Mapped[dict] = mapped_column(JSON, default=dict)   # {"instagram": "...", ...}
    gallery: Mapped[list] = mapped_column(JSON, default=list)         # [{"image_url", "description"}]

    payment_qr_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # timezone=True -> stored as TIMESTAMPTZ in Postgres. Every datetime the app
    # writes uses datetime.now(timezone.utc), so the column type must agree,
    # or asyncpg raises "can't subtract offset-naive and offset-aware datetimes".
    profile_completed: Mapped[bool] = mapped_column(default=False)
    is_configured: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list["UserSessionDB"]] = relationship(back_populates="user", cascade="all, delete-orphan")


@event.listens_for(UserDB, "before_insert")
@event.listens_for(UserDB, "before_update")
def _lowercase_email(mapper, connection, target: UserDB) -> None:
    if target.email:
        target.email = target.email.lower().strip()


class UserSessionDB(Base):
    """One row per issued refresh token. This is what makes logout real:
    logout flips revoked=True here, so that refresh token can never mint a new
    access token again -- i.e. it's blacklisted. Access tokens themselves are
    not blacklistable (stateless, short-lived by design), only refresh tokens."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    jti: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped["UserDB"] = relationship(back_populates="sessions")


class OtpCodeDB(Base):
    """Short-lived one-time codes for passwordless email login. We store only
    a hash, never the plaintext code -- same principle as a password hash,
    except these expire in minutes and are single-use, so there's never a
    'forgot your OTP' flow to build."""

    __tablename__ = "otp_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))

    attempts: Mapped[int] = mapped_column(default=0)
    consumed: Mapped[bool] = mapped_column(default=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# class UsageDB(Base):
#     __tablename__ = "institution_usage"
 
#     id: Mapped[int] = mapped_column(primary_key=True)
 
#     user_id: Mapped[uuid.UUID] = mapped_column(
#         Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
#     )
 
#     ai_credits_balance: Mapped[int] = mapped_column(BigInteger, default=0)
#     email_balance: Mapped[int] = mapped_column(default=200)
#     ai_voice_agents_balance_min: Mapped[int] = mapped_column(default=0)
#     storage_used_mb: Mapped[float] = mapped_column(default=0)
 
#     # --- NEW: embedding token budget (caps OpenAI embedding spend per tenant) ---
#     embedding_tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
#     embedding_token_limit: Mapped[int] = mapped_column(BigInteger, default=2800)
#     # --- END NEW ---
 
#     user: Mapped["UserDB"] = relationship(
#         back_populates="usage",
#         uselist=False,
#         lazy="selectin",
#     )


class LeadFormDB(Base):
    __tablename__ = "lead_forms"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(255))          # "Property Inquiry", "Booking"
    trigger_intent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # optional hint for when the bot should use THIS form vs another,
    # e.g. "site visit", "quote request" -- matched against user intent

    is_default: Mapped[bool] = mapped_column(default=False)
    requires_payment: Mapped[bool] = mapped_column(default=False)
    schema: Mapped[dict] = mapped_column(JSON)
    # ordered field defs, e.g.:
    # {"fields": [
    #    {"key": "name", "label": "Name", "type": "text", "prompt": "Could I get your name?", "required": true},
    #    {"key": "budget", "label": "Budget", "type": "number", "prompt": "What's your budget range?", "required": false}
    # ]}

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["UserDB"] = relationship()


class LeadDB(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lead_form_id: Mapped[uuid.UUID | None] = mapped_column(
    ForeignKey("lead_forms.id", ondelete="SET NULL"), nullable=True, index=True
)
    session_id: Mapped[str] = mapped_column(String(255), index=True)

    data: Mapped[dict] = mapped_column(JSON, default=dict)   # {"name": "...", "budget": "..."}
    status: Mapped[str] = mapped_column(String(50), default="in_progress")  # in_progress/complete
    payment_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # pending/verified/rejected

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())