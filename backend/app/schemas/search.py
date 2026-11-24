from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SearchEntity = Literal["project", "exploration", "page", "test_case", "workflow", "report"]
SearchWorkspace = Literal[
    "Projects",
    "Exploration",
    "Test Cases",
    "Workflow Intelligence",
    "Reports",
]


class SearchDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: SearchWorkspace
    project_id: UUID | None = None
    exploration_id: UUID | None = None
    page_id: UUID | None = None
    report_id: UUID | None = None


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: SearchEntity
    id: UUID
    title: str = Field(min_length=1, max_length=300)
    context: str = Field(max_length=500)
    destination: SearchDestination


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResult] = Field(max_length=20)
