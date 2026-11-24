from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage
from app.services.projects import (
    ArchivedProjectError,
    InvalidProjectAssociation,
    ensure_exploration_project_active,
)
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.schemas.test_execution import (
    TestCaseCollectionRead,
    TestCaseCreate,
    TestCaseSource,
    TestCaseUpdate,
    TestCaseRead,
    TestRunCreate,
    TestRunPageRead,
    TestRunRead,
    TestScenarioRead,
)
from app.services.test_execution_worker import TestExecutionManager
from app.services.test_planner import (
    CompiledScenario,
    InvalidScenario,
    compile_manual_login_smoke_test,
    compile_user_scenario,
    list_scenarios,
)
from app.services.test_case_service import (
    AnalysisNotReady,
    analysis_result_version_hash,
    compile_test_case_definition,
    create_user_created_test_case,
    duplicate_user_created_test_case,
    sync_ai_test_cases,
    update_test_case as update_test_case_record,
    upsert_user_created_test_case,
)
from app.services.test_interactions import load_interaction_observations

router = APIRouter(prefix="/explorations", tags=["browser test execution"])


def _get_exploration(session: Session, exploration_id: UUID) -> Exploration:
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        )
    return exploration


def _load_pages(session: Session, exploration_id: UUID) -> list[ExplorationPage]:
    return list(
        session.scalars(
            select(ExplorationPage)
            .options(selectinload(ExplorationPage.elements))
            .where(ExplorationPage.exploration_id == exploration_id)
            .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.url)
        )
    )


def _get_completed_analysis(session: Session, exploration_id: UUID) -> ExplorationAnalysis | None:
    analysis = session.scalar(
        select(ExplorationAnalysis).where(
            ExplorationAnalysis.exploration_id == exploration_id
        )
    )
    if analysis is None or analysis.status != "completed" or not isinstance(analysis.result, dict):
        return None
    return analysis


def _get_test_case(session: Session, exploration_id: UUID, case_id: UUID) -> TestCase:
    case = session.scalar(
        select(TestCase).where(
            TestCase.id == case_id,
            TestCase.exploration_id == exploration_id,
        )
    )
    if case is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "test_case_not_found", "message": "Test case was not found for this exploration."},
        )
    return case


def _validated_case_for_run(
    session: Session,
    exploration: Exploration,
    case: TestCase,
    pages: list[ExplorationPage],
) -> tuple[ExplorationAnalysis | None, CompiledScenario]:
    if case.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_deleted", "message": "Deleted test cases cannot be run."},
        )
    if not case.is_current:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_not_current", "message": "Superseded test cases cannot be run."},
        )

    analysis = None
    if case.source == "ai_generated":
        analysis = _get_completed_analysis(session, exploration.id)
        if analysis is None or analysis.id != case.source_analysis_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "analysis_not_completed", "message": "The AI test case is not available for the current completed analysis."},
            )
        try:
            sync_ai_test_cases(session, analysis, pages)
        except AnalysisNotReady as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "analysis_result_invalid", "message": str(exc)},
            ) from exc
        session.flush()
        if not case.is_current or case.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "test_case_not_current", "message": "The selected case is no longer current."},
            )
    else:
        # User-created cases are grounded in saved evidence and do not require AI.
        analysis = _get_completed_analysis(session, exploration.id)

    try:
        observation_map = load_interaction_observations(session, case.definition.get("scenario", {}))
        compiled = compile_test_case_definition(
            case.definition,
            pages,
            exploration.start_url,
            exploration_id=exploration.id,
            observations=observation_map,
            allow_observed_interactions=case.source == "user_created",
        )
    except (InvalidScenario, ValidationError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_invalid", "message": "The saved case no longer passes validation against persisted evidence."},
        ) from exc
    return analysis, compiled


def _ensure_project_accepts_run(session: Session, exploration_id: UUID) -> None:
    try:
        ensure_exploration_project_active(session, exploration_id)
    except ArchivedProjectError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_archived", "message": str(exc)},
        ) from exc
    except InvalidProjectAssociation as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_project_association", "message": str(exc)},
        ) from exc


def _persist_case_run(
    session: Session,
    case: TestCase,
    analysis: ExplorationAnalysis | None,
    compiled,
) -> TestRun:
    scenario_snapshot = {
        **case.definition,
        "source": case.source,
        "test_case_id": str(case.id),
        "source_analysis_id": str(case.source_analysis_id) if case.source_analysis_id else None,
        "analysis_version_hash": case.analysis_version_hash,
        "scenario_version_hash": case.scenario_version_hash,
    }
    run = TestRun(
        exploration_id=case.exploration_id,
        analysis_id=analysis.id if analysis is not None else None,
        test_case_id=case.id,
        scenario_index=-1,
        scenario=scenario_snapshot,
        status="pending",
    )
    session.add(run)
    session.flush()
    for step in compiled.steps:
        session.add(
            TestRunStep(
                test_run_id=run.id,
                ordinal=step.ordinal,
                instruction=step.instruction,
                action=step.action,
                expected_evidence=step.expected_evidence,
                status="pending",
            )
        )
    session.commit()
    return run


@router.get("/{exploration_id}/test-scenarios", response_model=list[TestScenarioRead])
def get_test_scenarios(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> list[dict]:
    _get_exploration(session, exploration_id)
    analysis = _get_completed_analysis(session, exploration_id)
    if analysis is None:
        return []
    try:
        return list_scenarios(analysis.result, _load_pages(session, exploration_id))
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "analysis_result_invalid", "message": "Saved AI scenarios could not be validated."},
        ) from exc


@router.get("/{exploration_id}/test-cases", response_model=TestCaseCollectionRead)
def get_test_cases(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    source: TestCaseSource | None = Query(default=None),
    case_status: Literal["active", "superseded", "deleted", "all"] = Query(default="active", alias="status"),
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """List persisted cases with stable filtering and pagination."""
    _get_exploration(session, exploration_id)
    analysis = session.scalar(
        select(ExplorationAnalysis).where(ExplorationAnalysis.exploration_id == exploration_id)
    )
    analysis_status = analysis.status if analysis is not None else "not_started"
    analysis_version_hash = None
    if analysis is not None and analysis.status == "completed":
        try:
            cases = sync_ai_test_cases(session, analysis, _load_pages(session, exploration_id))
            session.flush()
            analysis_version_hash = analysis_result_version_hash(analysis)
        except AnalysisNotReady as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "analysis_result_invalid", "message": str(exc)},
            ) from exc
        session.commit()
    statement = select(TestCase).where(
        TestCase.exploration_id == exploration_id,
    )
    if analysis_status != "completed":
        statement = statement.where(TestCase.source == "user_created")
    if source is not None:
        statement = statement.where(TestCase.source == source)
    if case_status == "active":
        statement = statement.where(TestCase.deleted_at.is_(None), TestCase.is_current.is_(True))
    elif case_status == "superseded":
        statement = statement.where(TestCase.deleted_at.is_(None), TestCase.is_current.is_(False))
    elif case_status == "deleted":
        statement = statement.where(TestCase.deleted_at.is_not(None))
    if search and search.strip():
        statement = statement.where(TestCase.name.ilike(f"%{search.strip()}%"))
    active_ai_case_count = session.scalar(
        select(func.count()).select_from(TestCase).where(
            TestCase.exploration_id == exploration_id,
            TestCase.source == "ai_generated",
            TestCase.is_current.is_(True),
            TestCase.deleted_at.is_(None),
        )
    ) or 0
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        session.scalars(
            statement.order_by(TestCase.created_at.desc(), TestCase.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return {
        "exploration_id": exploration_id,
        "analysis_status": analysis_status,
        "analysis_attempt_count": analysis.attempt_count if analysis is not None else 0,
        "analysis_error": analysis.error_message if analysis is not None else None,
        "analysis_version_hash": analysis_version_hash,
        "active_ai_case_count": active_ai_case_count,
        "cases": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post(
    "/{exploration_id}/test-cases",
    response_model=TestCaseRead,
    status_code=status.HTTP_201_CREATED,
)
def create_test_case(
    exploration_id: UUID,
    request_data: TestCaseCreate,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestCase:
    exploration = _get_exploration(session, exploration_id)
    if exploration.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_not_completed", "message": "Complete the browser exploration before creating a test case."},
        )
    try:
        pages = _load_pages(session, exploration_id)
        observation_map = load_interaction_observations(session, request_data.scenario)
        compiled = compile_user_scenario(
            request_data.scenario,
            exploration_id,
            pages,
            observation_map,
        )
    except InvalidScenario as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "manual_scenario_not_executable", "message": str(exc)},
        ) from exc
    case = create_user_created_test_case(
        session,
        exploration_id,
        compiled,
        name=request_data.name,
        description=request_data.description,
    )
    session.commit()
    session.refresh(case)
    return case


@router.get("/{exploration_id}/test-cases/{case_id}", response_model=TestCaseRead)
def get_test_case(
    exploration_id: UUID,
    case_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestCase:
    _get_exploration(session, exploration_id)
    return _get_test_case(session, exploration_id, case_id)


@router.patch("/{exploration_id}/test-cases/{case_id}", response_model=TestCaseRead)
def patch_test_case(
    exploration_id: UUID,
    case_id: UUID,
    request_data: TestCaseUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestCase:
    _get_exploration(session, exploration_id)
    case = _get_test_case(session, exploration_id, case_id)
    if case.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_deleted", "message": "Deleted test cases cannot be edited."},
        )
    fields = request_data.model_fields_set
    if ("name" in fields and request_data.name is None) or ("description" in fields and request_data.description is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "test_case_update_invalid", "message": "Name and description cannot be null."},
        )
    compiled = None
    if request_data.scenario is not None:
        if case.source == "ai_generated":
            raise HTTPException(
                status_code=409,
                detail={"code": "ai_case_scenario_read_only", "message": "Duplicate an AI case before changing its scenario."},
            )
        if case.definition.get("execution_kind") != "workflow":
            raise HTTPException(
                status_code=409,
                detail={"code": "scenario_kind_read_only", "message": "This saved case does not support editing its scenario steps."},
            )
        try:
            pages = _load_pages(session, exploration_id)
            observation_map = load_interaction_observations(session, request_data.scenario)
            compiled = compile_user_scenario(
                request_data.scenario,
                exploration_id,
                pages,
                observation_map,
            )
        except InvalidScenario as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "manual_scenario_not_executable", "message": str(exc)},
            ) from exc
    case = update_test_case_record(
        case,
        name=request_data.name,
        description=request_data.description,
        compiled=compiled,
    )
    session.commit()
    session.refresh(case)
    return case


@router.post(
    "/{exploration_id}/test-cases/{case_id}/duplicate",
    response_model=TestCaseRead,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_test_case(
    exploration_id: UUID,
    case_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestCase:
    exploration = _get_exploration(session, exploration_id)
    case = _get_test_case(session, exploration_id, case_id)
    if case.deleted_at is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_deleted", "message": "Deleted test cases cannot be duplicated."},
        )
    try:
        compiled = compile_test_case_definition(
            case.definition,
            _load_pages(session, exploration_id),
            exploration.start_url,
            exploration_id=exploration.id,
            observations=load_interaction_observations(session, case.definition.get("scenario", {})),
            allow_observed_interactions=case.source == "user_created",
        )
    except (InvalidScenario, ValidationError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_case_invalid", "message": "The saved case cannot be duplicated because it no longer validates."},
        ) from exc
    duplicate = duplicate_user_created_test_case(
        session,
        exploration_id,
        compiled,
        execution_kind=case.definition["execution_kind"],
        name=(f"Copy of {case.name}"[:200]),
        description=case.description,
    )
    session.commit()
    session.refresh(duplicate)
    return duplicate


@router.delete("/{exploration_id}/test-cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_case(
    exploration_id: UUID,
    case_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Response:
    _get_exploration(session, exploration_id)
    case = _get_test_case(session, exploration_id, case_id)
    if case.deleted_at is None:
        now = datetime.now(UTC)
        case.deleted_at = now
        case.updated_at = now
        session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{exploration_id}/test-runs",
    response_model=TestRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_test_run(
    exploration_id: UUID,
    request_data: TestRunCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestRun:
    exploration = _get_exploration(session, exploration_id)
    _ensure_project_accepts_run(session, exploration_id)
    if exploration.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_not_completed", "message": "Test execution requires a completed exploration."},
        )
    manager: TestExecutionManager | None = getattr(request.app.state, "test_execution_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "test_execution_worker_unavailable", "message": "The browser test worker is unavailable."},
        )
    pages = _load_pages(session, exploration_id)
    analysis: ExplorationAnalysis | None = None
    if request_data.manual_smoke_test is not None:
        analysis = _get_completed_analysis(session, exploration_id)
        try:
            compiled = compile_manual_login_smoke_test(
                request_data.manual_smoke_test.page_id,
                request_data.manual_smoke_test.login_button_element_id,
                pages,
                exploration.start_url,
            )
        except InvalidScenario as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "manual_scenario_not_executable", "message": str(exc)},
            ) from exc
        case = upsert_user_created_test_case(
            session,
            exploration_id,
            compiled,
            "manual_login_smoke",
        )
    else:
        assert request_data.test_case_id is not None
        case = _get_test_case(session, exploration_id, request_data.test_case_id)
        analysis, compiled = _validated_case_for_run(session, exploration, case, pages)

    run = _persist_case_run(session, case, analysis, compiled)
    manager.enqueue(run.id)
    return session.scalar(
        select(TestRun)
        .options(selectinload(TestRun.steps))
        .where(TestRun.id == run.id)
    )


@router.post(
    "/{exploration_id}/test-cases/{case_id}/runs",
    response_model=TestRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rerun_test_case(
    exploration_id: UUID,
    case_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestRun:
    """Create a new immutable run snapshot from the saved case as it exists now."""
    exploration = _get_exploration(session, exploration_id)
    _ensure_project_accepts_run(session, exploration_id)
    if exploration.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_not_completed", "message": "Test execution requires a completed exploration."},
        )
    manager: TestExecutionManager | None = getattr(request.app.state, "test_execution_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "test_execution_worker_unavailable", "message": "The browser test worker is unavailable."},
        )
    case = _get_test_case(session, exploration_id, case_id)
    analysis, compiled = _validated_case_for_run(
        session, exploration, case, _load_pages(session, exploration_id)
    )
    run = _persist_case_run(session, case, analysis, compiled)
    manager.enqueue(run.id)
    return session.scalar(
        select(TestRun)
        .options(selectinload(TestRun.steps))
        .where(TestRun.id == run.id)
    )


@router.get("/{exploration_id}/test-runs", response_model=list[TestRunRead])
def list_test_runs(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TestRun]:
    _get_exploration(session, exploration_id)
    return list(
        session.scalars(
            select(TestRun)
            .options(selectinload(TestRun.steps))
            .where(TestRun.exploration_id == exploration_id)
            .order_by(TestRun.created_at.desc(), TestRun.id)
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/{exploration_id}/test-run-history", response_model=TestRunPageRead)
def list_test_run_history(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    test_case_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return a stable page of run history, optionally scoped to a saved case."""
    _get_exploration(session, exploration_id)
    statement = select(TestRun).where(TestRun.exploration_id == exploration_id)
    if test_case_id is not None:
        statement = statement.where(TestRun.test_case_id == test_case_id)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    active_run_count = session.scalar(
        select(func.count()).select_from(TestRun).where(
            TestRun.exploration_id == exploration_id,
            TestRun.status.in_(("pending", "running")),
        )
    ) or 0
    items = list(
        session.scalars(
            statement.options(selectinload(TestRun.steps))
            .order_by(TestRun.created_at.desc(), TestRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "active_run_count": active_run_count,
    }


@router.get("/{exploration_id}/test-runs/{run_id}", response_model=TestRunRead)
def get_test_run(
    exploration_id: UUID,
    run_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestRun:
    _get_exploration(session, exploration_id)
    run = session.scalar(
        select(TestRun)
        .options(selectinload(TestRun.steps))
        .where(TestRun.id == run_id, TestRun.exploration_id == exploration_id)
    )
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "test_run_not_found", "message": "Test run was not found."},
        )
    return run


@router.post("/{exploration_id}/test-runs/{run_id}/cancel", response_model=TestRunRead)
async def cancel_test_run(
    exploration_id: UUID,
    run_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> TestRun:
    _get_exploration(session, exploration_id)
    run = session.get(TestRun, run_id)
    if run is None or run.exploration_id != exploration_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "test_run_not_found", "message": "Test run was not found."},
        )
    if run.status not in {"pending", "running"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "test_run_not_active", "message": "Only pending or running test runs can be cancelled."},
        )
    manager: TestExecutionManager | None = getattr(request.app.state, "test_execution_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "test_execution_worker_unavailable", "message": "The browser test worker is unavailable."},
        )
    await manager.cancel(run_id)
    session.expire_all()
    return session.scalar(
        select(TestRun)
        .options(selectinload(TestRun.steps))
        .where(TestRun.id == run_id, TestRun.exploration_id == exploration_id)
    )
