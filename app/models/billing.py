import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PlanDB(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)  # "free", "pro", "enterprise"
    conversation_limit: Mapped[int] = mapped_column(BigInteger, default=0)
    ai_credits_limit: Mapped[int] = mapped_column(BigInteger)
    email_limit: Mapped[int] = mapped_column(default=200)
    ai_voice_agents_limit_min: Mapped[int] = mapped_column(default=0)
    storage_limit_mb: Mapped[float] = mapped_column(default=0)
    embedding_token_limit: Mapped[int] = mapped_column(BigInteger, default=2800)


class UserPlanDB(Base):
    __tablename__ = "user_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    billing_period_start: Mapped[datetime]
    billing_period_end: Mapped[datetime]


class UsageDB(Base):
    __tablename__ = "usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # ALL consumption counters, ALL "used so far" style, consistently
    ai_credits_used: Mapped[int] = mapped_column(BigInteger, default=0)
    email_used: Mapped[int] = mapped_column(default=0)
    ai_voice_agents_used_min: Mapped[int] = mapped_column(default=0)
    storage_used_mb: Mapped[float] = mapped_column(default=0)
    embedding_tokens_used: Mapped[int] = mapped_column(BigInteger, default=0)
    conversation_done:Mapped[int] = mapped_column(Integer, default=0)