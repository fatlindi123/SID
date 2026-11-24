from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StructuredAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisEvidence(StructuredAnalysisModel):
    page_id: UUID
    element_ids: list[UUID] = Field(default_factory=list, max_length=30)


class WebsitePurpose(StructuredAnalysisModel):
    interpretation: str = Field(min_length=1, max_length=2000)
    evidence: list[AnalysisEvidence] = Field(min_length=1, max_length=30)


class PageSummary(StructuredAnalysisModel):
    page_id: UUID
    interpretation: str = Field(min_length=1, max_length=2000)
    evidence: list[AnalysisEvidence] = Field(min_length=1, max_length=30)


class NavigationInsight(StructuredAnalysisModel):
    evidence: AnalysisEvidence
    target_page_id: UUID | None = None
    interpretation: str = Field(min_length=1, max_length=1000)


class InteractiveElementInsight(StructuredAnalysisModel):
    evidence: AnalysisEvidence
    interpretation: str = Field(min_length=1, max_length=1000)


class FormAndFieldsInsight(StructuredAnalysisModel):
    page_id: UUID
    form_element_ids: list[UUID] = Field(default_factory=list, max_length=30)
    field_element_ids: list[UUID] = Field(default_factory=list, max_length=100)
    relationship_note: Literal["page_level_only"] = "page_level_only"


class WorkflowStep(StructuredAnalysisModel):
    instruction: str = Field(min_length=1, max_length=1000)
    evidence: list[AnalysisEvidence] = Field(min_length=1, max_length=10)


class WorkflowSuggestion(StructuredAnalysisModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    relevant_page_ids: list[UUID] = Field(min_length=1, max_length=30)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)
    expected_outcome: str = Field(min_length=1, max_length=1000)


class AnalysisWarning(StructuredAnalysisModel):
    collection: Literal[
        "main_navigation",
        "important_interactive_elements",
        "potential_workflows",
    ]
    discarded_count: int = Field(ge=1)


class AnalysisResult(StructuredAnalysisModel):
    website_purpose: WebsitePurpose
    page_summaries: list[PageSummary] = Field(max_length=100)
    main_navigation: list[NavigationInsight] = Field(max_length=100)
    important_interactive_elements: list[InteractiveElementInsight] = Field(max_length=100)
    forms_and_fields: list[FormAndFieldsInsight] = Field(max_length=100)
    potential_workflows: list[WorkflowSuggestion] = Field(max_length=50)
    warnings: list[AnalysisWarning] = Field(default_factory=list, max_length=3)


AnalysisStatus = Literal["not_started", "queued", "running", "completed", "failed"]


class AnalysisStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID | None = None
    exploration_id: UUID
    status: AnalysisStatus
    attempt_count: int = 0
    model: str | None = None
    error_message: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


class AnalysisResultsRead(BaseModel):
    id: UUID
    exploration_id: UUID
    model: str | None
    completed_at: datetime
    result: AnalysisResult
