from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_serializer, model_validator


ReportType = Literal["exploration", "project"]
ReportStatus = Literal["running", "completed", "failed"]


class ReportGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_created_from: AwareDatetime | None = None
    run_created_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "ReportGenerationRequest":
        if (
            self.run_created_from is not None
            and self.run_created_to is not None
            and self.run_created_from > self.run_created_to
        ):
            raise ValueError("run_created_from must be earlier than or equal to run_created_to.")
        return self


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_type: ReportType
    project_id: UUID | None
    exploration_id: UUID | None
    scope_label: str
    status: ReportStatus
    generated_at: datetime
    completed_at: datetime | None
    filters: dict[str, Any]
    snapshot: dict[str, Any] | None
    error_code: str | None

    @field_serializer("generated_at", "completed_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReportPageRead(BaseModel):
    items: list[ReportHistoryItem]
    total: int
    limit: int
    offset: int


class ReportHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    report_type: ReportType
    project_id: UUID | None
    exploration_id: UUID | None
    scope_label: str
    status: ReportStatus
    generated_at: datetime
    completed_at: datetime | None
    filters: dict[str, Any]
    error_code: str | None

    @field_serializer("generated_at", "completed_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
