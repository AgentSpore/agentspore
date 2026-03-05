"""Модель токен-транзакций."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TokenAction(str, enum.Enum):
    """Тип действия для начисления токенов."""

    IDEA_CREATED = "idea_created"  # +100
    VOTE = "vote"  # +5
    COMMENT = "comment"  # +10
    FEEDBACK = "feedback"  # +20
    FEATURE = "feature"  # +50
    CODE_CONTRIBUTION = "code_contribution"  # +100-500
    REFERRAL = "referral"  # +50
    BONUS = "bonus"  # variable


# Награды за каждое действие
TOKEN_REWARDS = {
    TokenAction.IDEA_CREATED: 100,
    TokenAction.VOTE: 5,
    TokenAction.COMMENT: 10,
    TokenAction.FEEDBACK: 20,
    TokenAction.FEATURE: 50,
    TokenAction.CODE_CONTRIBUTION: 100,  # базовое, может быть больше
    TokenAction.REFERRAL: 50,
    TokenAction.BONUS: 0,  # определяется динамически
}


class TokenTransaction(Base):
    """Транзакция токенов."""

    __tablename__ = "token_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    idea_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ideas.id"),
        nullable=True,
    )
    amount: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="token_transactions")
