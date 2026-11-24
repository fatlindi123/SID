"""Idempotent persistence and versioning for validated test case definitions."""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.exploration import ExplorationAnalysis, ExplorationPage
from app.models.interaction_observation import InteractionObservation
from app.models.test_case import TestCase
from app.schemas.analysis import AnalysisResult, WorkflowSuggestion
from app.schemas.test_execution import UserWorkflowSuggestion
from app.services.test_planner import (
    CompiledScenario,
    InvalidScenario,
    compile_manual_login_smoke_test,
    compile_scenario,
    compile_user_scenario,
)


class AnalysisNotReady(ValueError):
    """The persisted analysis is not a complete, valid source for test cases."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analysis_result_version_hash(analysis: ExplorationAnalysis) -> str:
    if analysis.status != "completed" or not isinstance(analysis.result, dict):
        raise AnalysisNotReady("Test cases require a completed analysis with a saved result.")
    try:
        result = AnalysisResult.model_validate(analysis.result)
    except Exception as exc:
        raise AnalysisNotReady("The saved analysis is incomplete or invalid.") from exc
    return _canonical_hash(result.model_dump(mode="json"))


def _definition(compiled: CompiledScenario, execution_kind: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "execution_kind": execution_kind,
        "scenario": compiled.scenario,
        "steps": [
            {
                "ordinal": step.ordinal,
                "instruction": step.instruction,
                "action": step.action,
                "expected_evidence": step.expected_evidence,
            }
            for step in compiled.steps
        ],
    }


def _identity_hash(scenario: dict[str, Any], execution_kind: str) -> str:
    """Fingerprint the logical case identity without using its AI list position."""
    first_step = (scenario.get("steps") or [{}])[0]
    first_evidence = (first_step.get("evidence") or [{}])[0]
    identity = {
        "execution_kind": execution_kind,
        "title": re.sub(r"\s+", " ", str(scenario.get("title", "")).strip()).casefold(),
        "objective": re.sub(r"\s+", " ", str(scenario.get("objective", "")).strip()).casefold(),
        "relevant_page_ids": sorted(str(value) for value in scenario.get("relevant_page_ids", [])),
        "first_instruction": re.sub(
            r"\s+", " ", str(first_step.get("instruction", "")).strip()
        ).casefold(),
        "first_evidence": {
            "page_id": str(first_evidence.get("page_id", "")),
            "element_ids": sorted(str(value) for value in first_evidence.get("element_ids", [])),
        },
        # The supported user-created smoke scenario does not have workflow
        # relevant_page_ids/steps, so include its direct evidence identity too.
        "page_id": str(scenario.get("page_id", "")),
        "login_button_element_id": str(scenario.get("login_button_element_id", "")),
    }
    return _canonical_hash(identity)


def _upsert_case(
    session: Session,
    *,
    exploration_id: UUID,
    source: str,
    scenario: dict[str, Any],
    definition: dict[str, Any],
    scenario_version_hash: str,
    source_analysis_id: UUID | None,
    analysis_version_hash: str | None,
    identity_hash: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> TestCase:
    case_identity_hash = identity_hash or _identity_hash(scenario, definition["execution_kind"])
    statement = (
        select(TestCase).where(
            TestCase.exploration_id == exploration_id,
            TestCase.source == source,
            TestCase.identity_hash == case_identity_hash,
        )
    )
    case = session.scalar(statement)
    if case is None:
        candidate = TestCase(
            # UUIDv5 makes retries and independently synchronized workers converge
            # on the same stable UUID for this logical case.
            id=uuid5(exploration_id, f"test-case:{source}:{case_identity_hash}"),
            exploration_id=exploration_id,
            source=source,
            identity_hash=case_identity_hash,
            source_analysis_id=source_analysis_id,
            analysis_version_hash=analysis_version_hash,
            scenario_version_hash=scenario_version_hash,
            name=name or str(scenario.get("title") or "Untitled test case")[:200],
            description=(description if description is not None else str(scenario.get("objective") or ""))[:4000],
            definition=definition,
            is_current=True,
        )
        try:
            # The unique constraint is the final guard if separate API requests
            # synchronize the same completed analysis at the same time.
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            return candidate
        except IntegrityError:
            case = session.scalar(statement)
            if case is None:
                raise

    changed = (
        case.source_analysis_id != source_analysis_id
        or case.analysis_version_hash != analysis_version_hash
        or case.scenario_version_hash != scenario_version_hash
        or case.definition != definition
        or not case.is_current
    )
    case.source_analysis_id = source_analysis_id
    case.analysis_version_hash = analysis_version_hash
    case.scenario_version_hash = scenario_version_hash
    case.definition = definition
    case.is_current = True
    if changed:
        case.updated_at = datetime.now(UTC)
    return case


def sync_ai_test_cases(
    session: Session,
    analysis: ExplorationAnalysis,
    pages: list[ExplorationPage],
) -> list[TestCase]:
    """Persist only executable workflows from a completed, validated analysis.

    Rows with changed definitions are updated in place under their stable logical
    identity. Existing TestRun scenario/step snapshots are independent JSON values.
    Cases that disappear or become unsafe are marked non-current, never deleted.
    """
    if analysis.status != "completed" or not isinstance(analysis.result, dict):
        raise AnalysisNotReady("Test cases require a completed analysis with a saved result.")
    if any(page.exploration_id != analysis.exploration_id for page in pages):
        raise AnalysisNotReady("Analysis pages must belong to the same exploration.")
    try:
        validated = AnalysisResult.model_validate(analysis.result)
    except Exception as exc:
        raise AnalysisNotReady("The saved analysis is incomplete or invalid.") from exc

    analysis_version_hash = _canonical_hash(validated.model_dump(mode="json"))
    compiled_cases: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
    seen_identities: set[str] = set()
    for workflow in validated.potential_workflows:
        try:
            compiled = compile_scenario(0, workflow, pages)
        except InvalidScenario:
            continue
        scenario = compiled.scenario
        definition = _definition(compiled, "workflow")
        identity_hash = _identity_hash(scenario, "workflow")
        if identity_hash in seen_identities:
            continue
        seen_identities.add(identity_hash)
        compiled_cases.append(
            (identity_hash, scenario, definition, _canonical_hash(definition))
        )

    existing = list(
        session.scalars(
            select(TestCase).where(
                TestCase.exploration_id == analysis.exploration_id,
                TestCase.source == "ai_generated",
                TestCase.is_current.is_(True),
            )
        )
    )
    retained = {identity_hash for identity_hash, *_ in compiled_cases}
    for case in existing:
        if case.identity_hash not in retained:
            case.is_current = False
            case.updated_at = datetime.now(UTC)

    output: list[TestCase] = []
    for identity_hash, scenario, definition, scenario_version_hash in compiled_cases:
        output.append(
            _upsert_case(
                session,
                exploration_id=analysis.exploration_id,
                source="ai_generated",
                scenario=scenario,
                definition=definition,
                scenario_version_hash=scenario_version_hash,
                source_analysis_id=analysis.id,
                analysis_version_hash=analysis_version_hash,
                identity_hash=identity_hash,
            )
        )
    return output


def upsert_user_created_test_case(
    session: Session,
    exploration_id: UUID,
    compiled: CompiledScenario,
    execution_kind: str,
) -> TestCase:
    """Persist the existing approved manual smoke scenario as a stable case."""
    definition = _definition(compiled, execution_kind)
    return _upsert_case(
        session,
        exploration_id=exploration_id,
        source="user_created",
        scenario=compiled.scenario,
        definition=definition,
        scenario_version_hash=_canonical_hash(definition),
        source_analysis_id=None,
        analysis_version_hash=None,
        name=str(compiled.scenario.get("title") or "Manual test case")[:200],
        description=str(compiled.scenario.get("objective") or "")[:4000],
    )


def _independent_identity(case_id: UUID) -> str:
    return _canonical_hash({"independent_test_case_id": str(case_id)})


def create_user_created_test_case(
    session: Session,
    exploration_id: UUID,
    compiled: CompiledScenario,
    *,
    name: str,
    description: str,
) -> TestCase:
    """Create a distinct user-owned case even when its scenario matches another case."""
    definition = _definition(compiled, "workflow")
    case_id = uuid4()
    case = TestCase(
        id=case_id,
        exploration_id=exploration_id,
        source="user_created",
        identity_hash=_independent_identity(case_id),
        source_analysis_id=None,
        analysis_version_hash=None,
        scenario_version_hash=_canonical_hash(definition),
        name=name,
        description=description,
        definition=definition,
        is_current=True,
    )
    session.add(case)
    session.flush()
    return case


def compile_test_case_definition(
    definition: dict[str, Any],
    pages: list[ExplorationPage],
    exploration_start_url: str,
    *,
    exploration_id: UUID | None = None,
    observations: dict[UUID, InteractionObservation] | None = None,
    allow_observed_interactions: bool = False,
) -> CompiledScenario:
    """Recompile a saved definition from persisted evidence and reject drift."""
    if not isinstance(definition, dict) or definition.get("schema_version") != 1:
        raise InvalidScenario("The saved test case definition is invalid.")
    execution_kind = definition.get("execution_kind")
    scenario = definition.get("scenario")
    saved_steps = definition.get("steps")
    if not isinstance(scenario, dict) or not isinstance(saved_steps, list):
        raise InvalidScenario("The saved test case execution plan is invalid.")
    if execution_kind == "workflow":
        if allow_observed_interactions:
            if exploration_id is None:
                raise InvalidScenario("The saved case has no exploration association.")
            workflow = UserWorkflowSuggestion.model_validate(scenario)
            compiled = compile_user_scenario(
                workflow,
                exploration_id,
                pages,
                observations or {},
            )
        else:
            workflow = WorkflowSuggestion.model_validate(scenario)
            compiled = compile_scenario(-1, workflow, pages)
    elif execution_kind == "manual_login_smoke":
        compiled = compile_manual_login_smoke_test(
            UUID(scenario["page_id"]),
            UUID(scenario["login_button_element_id"]),
            pages,
            exploration_start_url,
        )
    else:
        raise InvalidScenario("The saved test case action type is unsupported.")

    expected = _definition(compiled, execution_kind)
    if scenario != expected["scenario"] or saved_steps != expected["steps"]:
        raise InvalidScenario("The saved test case no longer matches persisted evidence.")
    return compiled


def duplicate_user_created_test_case(
    session: Session,
    exploration_id: UUID,
    compiled: CompiledScenario,
    *,
    execution_kind: str,
    name: str,
    description: str,
) -> TestCase:
    """Create a new independently identifiable user-owned copy."""
    definition = _definition(compiled, execution_kind)
    case_id = uuid4()
    case = TestCase(
        id=case_id,
        exploration_id=exploration_id,
        source="user_created",
        identity_hash=_independent_identity(case_id),
        source_analysis_id=None,
        analysis_version_hash=None,
        scenario_version_hash=_canonical_hash(definition),
        name=name,
        description=description,
        definition=definition,
        is_current=True,
    )
    session.add(case)
    session.flush()
    return case


def update_test_case(
    case: TestCase,
    *,
    name: str | None = None,
    description: str | None = None,
    compiled: CompiledScenario | None = None,
) -> TestCase:
    if name is not None:
        case.name = name
    if description is not None:
        case.description = description
    if compiled is not None:
        execution_kind = case.definition["execution_kind"]
        definition = _definition(compiled, execution_kind)
        case.definition = definition
        case.scenario_version_hash = _canonical_hash(definition)
    case.updated_at = datetime.now(UTC)
    return case
