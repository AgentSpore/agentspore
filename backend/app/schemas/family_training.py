"""Bounded training progress and public aggregate contracts, without contact data."""

from typing import Annotated, Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, StrictBool, model_validator

CurriculumVersion = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
TrainingLanguage = Literal[
    "en",
    "ru",
    "es",
    "pt",
    "fr",
    "de",
    "it",
    "nl",
    "pl",
    "uk",
    "cs",
    "sk",
    "ro",
    "hu",
    "el",
    "tr",
    "ar",
    "he",
    "fa",
    "hi",
    "bn",
    "ur",
    "id",
    "ms",
    "vi",
    "th",
    "ko",
    "ja",
    "zh-Hans",
    "sw",
    "tl",
]


class FamilyTrainingEvent(BaseModel):
    """A complete answer prefix; booleans express correctness, not button choice."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    event: Literal["training_progress"]
    attempt_id: UUID4
    curriculum_version: CurriculumVersion
    lang: TrainingLanguage
    answers: list[StrictBool] = Field(max_length=10)
    completed: StrictBool

    @model_validator(mode="after")
    def require_full_completion(self) -> Self:
        """Reject a completion that does not contain all ten answers."""
        if self.completed and len(self.answers) != 10:
            raise ValueError("Completion requires ten answers")
        return self


class TrainingRound(BaseModel):
    """Aggregate answers and errors for one ordered round."""

    round_id: int = Field(ge=1, le=10)
    answered: int = Field(ge=0)
    wrong: int = Field(ge=0)
    error_ratio: float | None = Field(ge=0, le=1)


class TrainingStats(BaseModel):
    """Seven-day first-seen cohort totals; no attempt or visitor identifiers."""

    curriculum_version: CurriculumVersion
    period_days: Literal[7] = 7
    starts: int = Field(ge=0)
    completions: int = Field(ge=0)
    completion_ratio: float | None = Field(ge=0, le=1)
    rounds: list[TrainingRound] = Field(min_length=10, max_length=10)
