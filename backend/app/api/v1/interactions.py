from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration, ExplorationPage
from app.models.interaction_observation import InteractionObservation
from app.schemas.exploration import InteractionObservationRead
from app.schemas.test_execution import EligibleInteractionObservationRead
from app.services.test_interactions import (
    InvalidInteractionObservation,
    validate_interaction_observation,
)

router = APIRouter(prefix="/explorations", tags=["interactions"])


@router.get(
    "/{exploration_id}/interactions",
    response_model=list[InteractionObservationRead],
)
def list_interaction_observations(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    page_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[InteractionObservation]:
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        )
    statement = select(InteractionObservation).where(
        InteractionObservation.exploration_id == exploration_id
    )
    if page_id is not None:
        page = session.scalar(
            select(ExplorationPage.id).where(
                ExplorationPage.id == page_id,
                ExplorationPage.exploration_id == exploration_id,
            )
        )
        if page is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "exploration_page_not_found", "message": "Page was not found in this exploration."},
            )
        statement = statement.where(InteractionObservation.page_id == page_id)
    return list(
        session.scalars(
            statement.order_by(
                InteractionObservation.created_at,
                InteractionObservation.id,
            ).limit(limit)
        )
    )


@router.get(
    "/{exploration_id}/test-observations",
    response_model=list[EligibleInteractionObservationRead],
)
def list_eligible_test_observations(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    page_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict]:
    """Return only observed tab/disclosure records with current saved evidence."""
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        )
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

    pages = list(
        session.scalars(
            select(ExplorationPage)
            .options(selectinload(ExplorationPage.elements))
            .where(ExplorationPage.exploration_id == exploration_id)
        )
    )
    statement = select(InteractionObservation).where(
        InteractionObservation.exploration_id == exploration_id
    )
    if page_id is not None:
        statement = statement.where(InteractionObservation.page_id == page_id)
    observations = list(
        session.scalars(
            statement.order_by(InteractionObservation.created_at, InteractionObservation.id).limit(limit)
        )
    )
    output = []
    for observation in observations:
        try:
            validated = validate_interaction_observation(observation, exploration_id, pages)
        except InvalidInteractionObservation:
            continue
        output.append(
            {
                "id": observation.id,
                "exploration_id": exploration_id,
                "page_id": validated.page.id,
                "page_url": validated.page_url,
                "page_title": (validated.page.title or "")[:160],
                "interaction_type": observation.action_type,
                "control": {
                    "element_id": validated.element.id,
                    "element_ordinal": validated.element.ordinal,
                    "tag_name": validated.tag_name,
                    "role": validated.role,
                    "name": validated.name,
                },
                "before_state": validated.state(validated.before_value),
                "observed_after_state": validated.state(validated.observed_after_value),
                "outcome": "observed",
            }
        )
    return output
