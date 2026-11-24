from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.exploration import ExplorationPage
from app.schemas.knowledge_graph import (
    GraphNodeType,
    GraphProvenance,
    KnowledgeGraphBuildRead,
    KnowledgeGraphRead,
)
from app.services.knowledge_graph import GraphSourceNotFound, read_knowledge_graph, synchronize_knowledge_graph

router = APIRouter(prefix="/explorations", tags=["knowledge graph"])


@router.get("/{exploration_id}/knowledge-graph", response_model=KnowledgeGraphRead)
def get_knowledge_graph(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    node_type: GraphNodeType | None = None,
    page_id: UUID | None = None,
    provenance: GraphProvenance | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> dict:
    if page_id is not None and session.scalar(
        select(ExplorationPage.id).where(
            ExplorationPage.id == page_id,
            ExplorationPage.exploration_id == exploration_id,
        )
    ) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_page_not_found", "message": "Page was not found in this exploration."},
        )
    try:
        return read_knowledge_graph(
            session,
            exploration_id,
            node_type=node_type,
            page_id=page_id,
            provenance=provenance,
            limit=limit,
        )
    except GraphSourceNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        ) from exc


@router.post("/{exploration_id}/knowledge-graph/rebuild", response_model=KnowledgeGraphBuildRead)
def rebuild_knowledge_graph(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        result = synchronize_knowledge_graph(session, exploration_id)
        session.commit()
        return result
    except GraphSourceNotFound as exc:
        session.rollback()
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        ) from exc
    except Exception:
        session.rollback()
        raise
