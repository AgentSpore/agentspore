"""Модели идеи и голосования."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class IdeaStatus(str, enum.Enum):
    """Статус идеи."""

    DRAFT = "draft"
    VOTING = "voting"
    SANDBOX = "sandbox"
    BUILDING = "building"
    LAUNCHED = "launched"
    ARCHIVED = "archived"


class Idea(Base):
    """Идея стартапа."""

    __tablename__ = "ideas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(200))
    problem: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(50), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
    )
    votes_up: Mapped[int] = mapped_column(Integer, default=0)
    votes_down: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(50), default="voting")
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    author: Mapped["User"] = relationship("User", back_populates="ideas")
    votes: Mapped[list["Vote"]] = relationship("Vote", back_populates="idea")
    sandbox: Mapped["Sandbox"] = relationship("Sandbox", back_populates="idea", uselist=False)
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="idea")

    @property
    def score(self) -> int:
        """Общий рейтинг идеи."""
        return self.votes_up - self.votes_down


class Vote(Base):
    """Голос за идею."""

    __tablename__ = "votes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
    )
    idea_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ideas.id"),
    )
    value: Mapped[int] = mapped_column(Integer)  # 1 = upvote, -1 = downvote
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="votes")
    idea: Mapped["Idea"] = relationship("Idea", back_populates="votes")


class Comment(Base):
    """Комментарий к идее."""

    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
    )
    idea_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ideas.id"),
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    user: Mapped["User"] = relationship("User")
    idea: Mapped["Idea"] = relationship("Idea", back_populates="comments")
