from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal[
    "Referral",
    "Lab Result",
    "Imaging Report",
    "Prior Authorization",
    "Refill Request",
    "Records Request",
    "EOB",
    "Needs Review",
]
Action = Literal["routed", "needs_review", "skipped_duplicate", "already_routed", "failed"]


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fax(Model):
    fax_id: str
    received_at: str
    sender: str
    expected_pages: int = Field(ge=1)
    status: Literal["New", "Routed"]


class Line(Model):
    text: str
    confidence: float = Field(ge=0, le=100)


class PageText(Model):
    number: int = Field(ge=1)
    text: str
    confidence: float = Field(ge=0, le=100)
    lines: list[Line] = Field(default_factory=list)
    method: str
    warnings: list[str] = Field(default_factory=list)


class Evidence(Model):
    value: str
    page: int
    quote: str
    confidence: float


class Extraction(Model):
    engine: str
    pages: list[PageText]
    classification: Category
    categories: list[Category] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    source_ids: list[Evidence] = Field(default_factory=list)
    patient_names: list[Evidence] = Field(default_factory=list)
    dates_of_birth: list[Evidence] = Field(default_factory=list)
    resend_evidence: list[Evidence] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class Route(Model):
    assigned_to: str
    document_type: Category
    document_folder: str


class Event(Model):
    at: str = Field(default_factory=timestamp)
    stage: str
    detail: str
    context_id: str | None = None


class Record(Model):
    fax: Fax
    downloaded_fax: Fax | None = None
    pdf_path: str | None = None
    pdf_sha256: str | None = None
    downloaded_at: str | None = None
    extraction: Extraction | None = None
    action: Action | None = None
    selected: Route | None = None
    observed: Route | None = None
    duplicate_of: str | None = None
    verification: Literal["pending", "verified", "conflict", "failed"] = "pending"
    verified_at: str | None = None
    submission_pending: bool = False
    submit_attempts: int = 0
    errors: list[Event] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

    def event(self, stage: str, detail: str, context_id: str | None = None) -> None:
        self.events.append(Event(stage=stage, detail=detail, context_id=context_id))

    def error(self, stage: str, detail: str) -> None:
        self.errors.append(Event(stage=stage, detail=detail))
