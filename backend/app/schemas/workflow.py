from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WorkflowProvenance = Literal["observed", "suggested", "user_authored"]
WorkflowStatusFilter = Literal["active", "stale", "reviewed", "unreviewed"]


class WorkflowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("Name cannot be empty.")
        return value

    @model_validator(mode="after")
    def require_update_fields(self) -> "WorkflowUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide a name or description to update.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Name and description cannot be null.")
        return self


class WorkflowStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    step_type: str
    provenance: WorkflowProvenance
    instruction: str
    page_id: UUID
    target_page_id: UUID | None
    page_url: str
    page_title: str
    control_name: str | None
    evidence: list[dict[str, Any]]
    observed_state: dict[str, Any] | None
    expected_state: dict[str, Any] | None


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    project_id: UUID | None
    identity_hash: str
    source_type: Literal["page_link", "interaction_observation", "test_case", "manual"]
    source_id: str
    source_hash: str
    provenance: WorkflowProvenance
    status: Literal["active", "stale"]
    name: str
    description: str
    reviewed: bool
    reviewed_at: datetime | None
    evidence: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    steps: list[WorkflowStepRead] = Field(default_factory=list)


class WorkflowPageRead(BaseModel):
    items: list[WorkflowRead] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class WorkflowRebuildRead(BaseModel):
    exploration_id: UUID
    created: int
    updated: int
    unchanged: int
    marked_stale: int
    preserved_manual: int
    skipped_invalid_sources: int


class ProjectWorkflowRebuildRead(BaseModel):
    project_id: UUID
    rebuilt_explorations: int
    skipped_incomplete_explorations: int
    created: int
    updated: int
    unchanged: int
    marked_stale: int
    preserved_manual: int
    skipped_invalid_sources: int
