from datetime import datetime
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_target_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Target website must contain a valid port.") from exc
    if (
        len(cleaned) > 2048
        or parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in cleaned)
    ):
        raise ValueError("Target website must be a valid HTTP or HTTPS URL without embedded credentials.")
    return cleaned


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    target_url: str = Field(min_length=1, max_length=2048)
    description: str = Field(default="", max_length=4000)
    environment: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Project name cannot be empty.")
        return cleaned

    @field_validator("target_url")
    @classmethod
    def valid_target_url(cls, value: str) -> str:
        return _validate_target_url(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("environment")
    @classmethod
    def clean_environment(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        return cleaned or None


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_url: str | None = Field(default=None, min_length=1, max_length=2048)
    description: str | None = Field(default=None, max_length=4000)
    environment: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Project name cannot be empty.")
        return cleaned

    @field_validator("target_url")
    @classmethod
    def valid_target_url(cls, value: str | None) -> str | None:
        return _validate_target_url(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("environment")
    @classmethod
    def clean_environment(cls, value: str | None) -> str | None:
        cleaned = value.strip() if value is not None else None
        return cleaned or None

    @model_validator(mode="after")
    def has_changes(self) -> "ProjectUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one project field to update.")
        return self


class ProjectStats(BaseModel):
    exploration_count: int = 0
    active_exploration_count: int = 0
    completed_exploration_count: int = 0
    failed_exploration_count: int = 0
    test_case_count: int = 0
    test_run_count: int = 0
    pending_run_count: int = 0
    running_run_count: int = 0
    passed_run_count: int = 0
    failed_run_count: int = 0
    knowledge_graph_exploration_count: int = 0


class ProjectRead(BaseModel):
    id: UUID
    name: str
    target_url: str
    description: str
    environment: str | None
    archived: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    stats: ProjectStats


class ProjectPageRead(BaseModel):
    items: list[ProjectRead]
    total: int
    limit: int
    offset: int


class ProjectExplorationSummaryRead(BaseModel):
    id: UUID
    start_url: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    pages_discovered: int
    elements_discovered: int
    event_count: int
    created_at: datetime
    completed_at: datetime | None
    test_case_count: int
    test_run_count: int
    knowledge_graph_available: bool


class ProjectExplorationPageRead(BaseModel):
    items: list[ProjectExplorationSummaryRead]
    total: int
    limit: int
    offset: int


class ProjectTestCaseSummaryRead(BaseModel):
    id: UUID
    exploration_id: UUID
    exploration_url: str
    source: Literal["ai_generated", "user_created"]
    status: Literal["active", "superseded", "deleted"]
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class ProjectTestCasePageRead(BaseModel):
    items: list[ProjectTestCaseSummaryRead]
    total: int
    limit: int
    offset: int


class ProjectRunSummaryRead(BaseModel):
    id: UUID
    exploration_id: UUID
    exploration_url: str
    test_case_id: UUID | None
    status: Literal["pending", "running", "passed", "failed", "cancelled"]
    error_message: str | None
    step_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ProjectRunPageRead(BaseModel):
    items: list[ProjectRunSummaryRead]
    total: int
    limit: int
    offset: int


class ProjectGraphAvailabilityRead(BaseModel):
    exploration_id: UUID
    exploration_url: str
    exploration_status: Literal["queued", "running", "completed", "failed", "cancelled"]
    node_count: int
    edge_count: int
    available: bool


class ProjectGraphPageRead(BaseModel):
    items: list[ProjectGraphAvailabilityRead]
    total: int
    limit: int
    offset: int


class ProjectExplorationAssociationRead(BaseModel):
    project_id: UUID
    exploration_id: UUID
    created_at: datetime
