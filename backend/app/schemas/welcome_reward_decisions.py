"""Bounded operator attestations and safe decision receipts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

OpaqueReference = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]


class WelcomeRewardDecision(BaseModel):
    """Bind a reviewed snapshot; references are attestations, not authentication."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    grant_id: UUID
    owner_user_id: UUID
    source_event: UUID
    policy_version: Annotated[str, Field(min_length=1, max_length=80)]
    outcome: Literal["allow", "hold", "deny"]
    beneficiary_key: OpaqueReference | None
    authority_ref: OpaqueReference
    evidence_ref: OpaqueReference
    expected_revision: Annotated[int, Field(ge=0, lt=9223372036854775807)]
    email_proof_at: AwareDatetime | None
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def check_terms(self) -> Self:
        """Reject incomplete admission and inverted validity intervals."""
        if self.expires_at <= self.issued_at:
            raise ValueError("invalid_interval")
        if self.outcome == "allow" and self.beneficiary_key is None:
            raise ValueError("beneficiary_required")
        return self

    def matches(self, row: dict) -> bool:
        """Compare all evidence, including the revision the operator reviewed."""
        return self.expected_revision == row["revision"] - 1 and all(
            value == row[key]
            for key, value in self.model_dump(exclude={"expected_revision"}).items()
        )


class WelcomeRewardDecisionResult(BaseModel):
    """Expose only a fixed verdict and technical receipt identifiers."""

    verdict: Literal[
        "recorded", "replayed", "conflict", "stale", "not_found", "invalid_input", "ineligible"
    ]
    decision_id: int | None = None
    revision: int | None = None

    @classmethod
    def receipt(cls, verdict: Literal["recorded", "replayed"], row: dict) -> Self:
        """Build a receipt without copying private references."""
        return cls(verdict=verdict, decision_id=row["id"], revision=row["revision"])


class WelcomeAutoDecisionResult(BaseModel):
    """Counts of automatic approvals and applications left for manual review."""

    verdict: Literal["auto_decided"] = "auto_decided"
    approved: int
    left: int


class BeneficiaryAssessment(BaseModel):
    """Canonical beneficiary key and the ordered reasons that keep an allow on hold."""

    model_config = ConfigDict(frozen=True)

    beneficiary_key: str
    holds: tuple[str, ...]
