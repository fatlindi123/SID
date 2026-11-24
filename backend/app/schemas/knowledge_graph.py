from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


GraphNodeType = Literal[
    "exploration",
    "page",
    "ui_element",
    "form",
    "workflow",
    "test_case",
    "interaction_state",
]
GraphEdgeType = Literal[
    "contains_page",
    "contains_element",
    "links_to",
    "belongs_to_form",
    "participates_in_workflow",
    "has_test_case",
    "transitions_to_state",
]
GraphProvenance = Literal["verified", "suggested"]


class GraphEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal[
        "exploration",
        "page",
        "element",
        "analysis",
        "test_case",
        "interaction_observation",
    ]
    source_id: str = Field(min_length=1, max_length=300)
    page_id: UUID | None = None
    element_id: UUID | None = None


class KnowledgeGraphNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    node_type: GraphNodeType
    source_key: str
    label: str
    page_id: UUID | None
    provenance: GraphProvenance
    evidence: list[GraphEvidenceReference] = Field(min_length=1)
    properties: dict[str, Any]


class KnowledgeGraphEdgeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    exploration_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    edge_type: GraphEdgeType
    provenance: GraphProvenance
    evidence: list[GraphEvidenceReference] = Field(min_length=1)
    properties: dict[str, Any]


class KnowledgeGraphRead(BaseModel):
    exploration_id: UUID
    nodes: list[KnowledgeGraphNodeRead]
    edges: list[KnowledgeGraphEdgeRead]
    total_nodes: int
    total_edges: int
    returned_nodes: int
    truncated: bool


class KnowledgeGraphBuildRead(BaseModel):
    exploration_id: UUID
    nodes_written: int
    edges_written: int
    verified_nodes: int
    suggested_nodes: int
    verified_edges: int
    suggested_edges: int
    skipped_invalid_workflows: int
    skipped_invalid_interactions: int
