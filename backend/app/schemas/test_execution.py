from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.analysis import AnalysisEvidence


ExecutionStatus = Literal["pending", "running", "passed", "failed", "cancelled"]


class ManualLoginSmokeTestCreate(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    page_id: UUID
    login_button_element_id: UUID


class TestRunCreate(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID | None = None
    manual_smoke_test: ManualLoginSmokeTestCreate | None = None

    @model_validator(mode="after")
    def require_one_scenario_source(self) -> "TestRunCreate":
        if (self.test_case_id is None) == (self.manual_smoke_test is None):
            raise ValueError("Choose exactly one saved test case or supported manual smoke test.")
        return self


TestCaseSource = Literal["ai_generated", "user_created"]
TestCaseStatus = Literal["active", "superseded", "deleted"]
AnalysisCaseStatus = Literal["not_started", "queued", "running", "completed", "failed"]


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Name cannot be empty.")
    return cleaned


class TestCaseCreate(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    scenario: UserWorkflowSuggestion

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _clean_name(value) or ""

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class TestCaseUpdate(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    scenario: UserWorkflowSuggestion | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _clean_name(value)

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_update_fields(self) -> "TestCaseUpdate":
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update.")
        return self


class ObservationStepSelection(BaseModel):
    """A user-confirmed expected state bound to one saved observation."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    observation_id: UUID
    expected_state: bool
    expected_state_confirmed: Literal[True]


class UserWorkflowStep(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    instruction: str = Field(default="", max_length=1000)
    evidence: list[AnalysisEvidence] = Field(min_length=1, max_length=10)
    interaction_observation: ObservationStepSelection | None = None
    interaction_evidence_snapshot: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_instruction_for_regular_steps(self) -> "UserWorkflowStep":
        if self.interaction_observation is None and not self.instruction.strip():
            raise ValueError("A supported action instruction is required.")
        return self


class UserWorkflowSuggestion(BaseModel):
    """Manual case scenario shape; AI analysis continues to use WorkflowSuggestion."""

    __test__ = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1000)
    relevant_page_ids: list[UUID] = Field(min_length=1, max_length=30)
    steps: list[UserWorkflowStep] = Field(min_length=1, max_length=20)
    expected_outcome: str = Field(min_length=1, max_length=1000)


class EligibleObservationControlRead(BaseModel):
    __test__ = False

    element_id: UUID
    element_ordinal: int
    tag_name: str
    role: str
    name: str


class EligibleObservationStateRead(BaseModel):
    __test__ = False

    selected: bool | None = None
    expanded: bool | None = None


class EligibleInteractionObservationRead(BaseModel):
    __test__ = False

    id: UUID
    exploration_id: UUID
    page_id: UUID
    page_url: str
    page_title: str
    interaction_type: Literal["tab", "disclosure"]
    control: EligibleObservationControlRead
    before_state: EligibleObservationStateRead
    observed_after_state: EligibleObservationStateRead
    outcome: Literal["observed"]


TestCaseCreate.model_rebuild()
TestCaseUpdate.model_rebuild()


class TestCaseRead(BaseModel):
    __test__ = False
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    source: TestCaseSource
    status: TestCaseStatus
    name: str
    description: str
    identity_hash: str
    source_analysis_id: UUID | None
    analysis_version_hash: str | None
    scenario_version_hash: str
    definition: dict[str, Any]
    is_current: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TestCaseCollectionRead(BaseModel):
    __test__ = False

    exploration_id: UUID
    analysis_status: AnalysisCaseStatus
    analysis_attempt_count: int = 0
    analysis_error: str | None = None
    analysis_version_hash: str | None = None
    active_ai_case_count: int = 0
    cases: list[TestCaseRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class TestScenarioRead(BaseModel):
    __test__ = False
    scenario_index: int
    title: str
    objective: str
    expected_outcome: str
    relevant_page_ids: list[UUID]
    steps: list[dict[str, Any]]
    executable: bool
    unavailable_reason: str | None = None


class TestRunStepRead(BaseModel):
    __test__ = False
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ordinal: int
    instruction: str
    action: dict[str, Any]
    expected_evidence: dict[str, Any]
    status: ExecutionStatus
    duration_ms: int | None
    error_message: str | None
    actual_evidence: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None


class TestRunRead(BaseModel):
    __test__ = False
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    analysis_id: UUID | None
    test_case_id: UUID | None
    scenario_index: int
    scenario: dict[str, Any]
    status: ExecutionStatus
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[TestRunStepRead] = Field(default_factory=list)


class TestRunPageRead(BaseModel):
    __test__ = False

    items: list[TestRunRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0
    active_run_count: int = 0
