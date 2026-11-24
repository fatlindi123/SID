"""Strict provider output schema; persisted forms/fields are assembled from DB facts."""

from typing import Any

from pydantic import Field

from app.schemas.analysis import (
    AnalysisEvidence,
    InteractiveElementInsight,
    NavigationInsight,
    PageSummary,
    StructuredAnalysisModel,
    WebsitePurpose,
    WorkflowSuggestion,
)


class AnalysisDraft(StructuredAnalysisModel):
    website_purpose: WebsitePurpose
    page_summaries: list[PageSummary] = Field(max_length=100)
    main_navigation: list[NavigationInsight] = Field(max_length=100)
    important_interactive_elements: list[InteractiveElementInsight] = Field(max_length=100)
    potential_workflows: list[WorkflowSuggestion] = Field(max_length=50)


class AnalysisDraftPayload(StructuredAnalysisModel):
    """Strict core fields with raw optional collections for item-level validation."""

    website_purpose: WebsitePurpose
    page_summaries: list[PageSummary] = Field(max_length=100)
    main_navigation: Any = Field(default_factory=list)
    important_interactive_elements: Any = Field(default_factory=list)
    potential_workflows: Any = Field(default_factory=list)
