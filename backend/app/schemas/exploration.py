from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ExplorationStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
InteractionOutcome = Literal["observed", "skipped", "blocked", "failed"]


class ExplorationCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ExplorationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    start_url: str
    status: ExplorationStatus
    current_url: str | None
    max_pages: int
    max_depth: int
    pages_discovered: int
    elements_discovered: int
    event_count: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class ObservedElementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    kind: str
    tag_name: str
    role: str | None
    accessible_name: str | None
    label: str | None
    text: str | None
    selector: str | None
    attributes: dict[str, Any]


class ExplorationPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    title: str
    description: str | None
    depth: int
    http_status: int | None
    visible_text: str
    dom_outline: list[dict[str, Any]]
    element_count: int
    screenshot_url: str | None = None
    discovered_at: datetime
    elements: list[ObservedElementRead] = Field(default_factory=list)


class ExplorationEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    event_type: str
    message: str
    url: str | None
    details: dict[str, Any]
    created_at: datetime


class InteractionObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    page_id: UUID
    element_ordinal: int
    action_type: Literal["tab", "disclosure", "unknown"]
    outcome: InteractionOutcome
    reason: str | None
    before_evidence: dict[str, Any]
    after_evidence: dict[str, Any] | None
    duration_ms: int
    created_at: datetime
