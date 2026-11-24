"""Persist deterministic workflows derived only from validated saved evidence."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin
from uuid import UUID, uuid5

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.test_navigation import is_safe_navigation_target, normalized_navigation_label
from app.core.url_security import UnsafeTargetUrl, normalized_http_url, same_origin
from app.models.exploration import (
    Exploration,
    ExplorationAnalysis,
    ExplorationEvent,
    ExplorationPage,
    ObservedElement,
)
from app.models.interaction_observation import InteractionObservation
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.project import Project, ProjectExploration, ProjectMetadata
from app.models.test_case import TestCase
from app.models.workflow import Workflow, WorkflowStep
from app.schemas.analysis import AnalysisEvidence, WorkflowStep as AnalysisWorkflowStep, WorkflowSuggestion
from app.services.test_case_service import AnalysisNotReady, analysis_result_version_hash, compile_test_case_definition
from app.services.test_interactions import (
    InvalidInteractionObservation,
    load_interaction_observations,
    validate_interaction_observation,
)
from app.services.test_planner import InvalidScenario, compile_scenario


_WORKFLOW_NAMESPACE = UUID("b72cb7b6-4f71-5a52-8e23-28fa83e0c785")


class WorkflowNotFound(LookupError):
    pass


class WorkflowSourceNotFound(LookupError):
    pass


class WorkflowRebuildUnavailable(ValueError):
    pass


def _hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()


def _identity(exploration_id: UUID, source_type: str, source_id: str) -> tuple[str, UUID]:
    identity_hash = _hash({"source_type": source_type, "source_id": source_id})
    return identity_hash, uuid5(_WORKFLOW_NAMESPACE, f"{exploration_id}:{identity_hash}")


def _project_id(session: Session, exploration_id: UUID) -> UUID | None:
    return session.scalar(
        select(ProjectExploration.project_id).where(ProjectExploration.exploration_id == exploration_id)
    )


def sync_workflow_project_association(session: Session, exploration_id: UUID, project_id: UUID | None) -> None:
    """Keep workflow-side project metadata aligned with the authoritative assignment row."""
    rows = session.scalars(select(Workflow).where(Workflow.exploration_id == exploration_id))
    for workflow in rows:
        workflow.project_id = project_id


def _page_url(page: ExplorationPage) -> str | None:
    try:
        return normalized_http_url(page.url)
    except (UnsafeTargetUrl, TypeError, ValueError):
        return None


def _page_evidence(page_id: UUID) -> dict[str, str]:
    return {"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}


def _element_evidence(page_id: UUID, element_id: UUID) -> dict[str, str]:
    return {
        "source_type": "element",
        "source_id": str(element_id),
        "page_id": str(page_id),
        "element_id": str(element_id),
    }


def _step_values(
    *,
    ordinal: int,
    step_type: str,
    provenance: str,
    instruction: str,
    page: ExplorationPage,
    evidence: list[dict[str, Any]],
    target_page: ExplorationPage | None = None,
    control_name: str | None = None,
    observed_state: dict[str, Any] | None = None,
    expected_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "step_type": step_type,
        "provenance": provenance,
        "instruction": instruction[:1000],
        "page_id": page.id,
        "target_page_id": target_page.id if target_page else None,
        "page_url": _page_url(page) or page.url,
        "page_title": (page.title or "")[:1000],
        "control_name": (control_name[:200] if control_name else None),
        "evidence": evidence,
        "observed_state": observed_state,
        "expected_state": expected_state,
    }


def _graph_link_evidence(
    edges: list[KnowledgeGraphEdge],
    nodes_by_id: dict[UUID, KnowledgeGraphNode],
    source_page: ExplorationPage,
    target_page: ExplorationPage,
    element: ObservedElement,
) -> dict[str, str] | None:
    for edge in edges:
        if edge.edge_type != "links_to" or edge.provenance != "verified":
            continue
        source = nodes_by_id.get(edge.source_node_id)
        target = nodes_by_id.get(edge.target_node_id)
        if not source or not target or source.node_type != "page" or target.node_type != "page":
            continue
        if source.source_key != str(source_page.id) or target.source_key != str(target_page.id):
            continue
        refs = edge.evidence if isinstance(edge.evidence, list) else []
        has_source = any(
            item.get("source_type") == "page" and item.get("source_id") == str(source_page.id)
            and item.get("page_id") == str(source_page.id)
            for item in refs if isinstance(item, dict)
        )
        has_target = any(
            item.get("source_type") == "page" and item.get("source_id") == str(target_page.id)
            and item.get("page_id") == str(target_page.id)
            for item in refs if isinstance(item, dict)
        )
        has_link = any(
            item.get("source_type") == "element"
            and item.get("source_id") == str(element.id)
            and item.get("page_id") == str(source_page.id)
            and item.get("element_id") == str(element.id)
            for item in refs if isinstance(item, dict)
        )
        if has_source and has_target and has_link:
            return {"source_type": "knowledge_graph_edge", "source_id": str(edge.id)}
    return None


def _event_failed_urls(events: list[ExplorationEvent]) -> set[str]:
    failed: set[str] = set()
    for event in events:
        if event.event_type not in {"page_failed", "navigation_blocked", "page_timeout"} or not event.url:
            continue
        try:
            failed.add(normalized_http_url(event.url))
        except (UnsafeTargetUrl, TypeError, ValueError):
            continue
    return failed


def _collect_page_link_sources(
    exploration: Exploration,
    pages: list[ExplorationPage],
    edges: list[KnowledgeGraphEdge],
    nodes: list[KnowledgeGraphNode],
    events: list[ExplorationEvent],
) -> tuple[list[dict[str, Any]], int]:
    pages_by_url = {url: page for page in pages if (url := _page_url(page)) is not None}
    nodes_by_id = {item.id: item for item in nodes}
    failed_urls = _event_failed_urls(events)
    sources: list[dict[str, Any]] = []
    skipped = 0
    for page in pages:
        page_url = _page_url(page)
        if page_url is None or not same_origin(exploration.start_url, page_url):
            continue
        for element in page.elements:
            attributes = element.attributes if isinstance(element.attributes, dict) else {}
            if element.kind != "link" or element.tag_name.casefold() != "a" or element.role != "link":
                continue
            if attributes.get("inside_form") is not False or normalized_navigation_label(element.accessible_name) is None:
                skipped += 1
                continue
            href = attributes.get("href")
            if not isinstance(href, str):
                skipped += 1
                continue
            try:
                target_url = normalized_http_url(urljoin(page.url, href))
            except (UnsafeTargetUrl, TypeError, ValueError):
                skipped += 1
                continue
            target_page = pages_by_url.get(target_url)
            if (
                target_page is None
                or target_page.id == page.id
                or not same_origin(exploration.start_url, target_url)
                or page_url in failed_urls
                or not is_safe_navigation_target(page_url)
                or not is_safe_navigation_target(target_url)
                or target_url in failed_urls
                or (page.http_status is not None and not 200 <= page.http_status < 400)
                or (target_page.http_status is not None and not 200 <= target_page.http_status < 400)
            ):
                skipped += 1
                continue
            label = normalized_navigation_label(element.accessible_name)
            assert label is not None
            scenario = WorkflowSuggestion(
                title=f"Open {label.title()}",
                objective="Review the saved page reached by this captured internal link.",
                relevant_page_ids=[page.id, target_page.id],
                steps=[AnalysisWorkflowStep(
                    instruction=f"Open the {label} link.",
                    evidence=[AnalysisEvidence(page_id=page.id, element_ids=[element.id])],
                )],
                expected_outcome="The linked target page is present in the saved exploration.",
            )
            try:
                compiled = compile_scenario(0, scenario, pages)
            except (InvalidScenario, ValueError):
                skipped += 1
                continue
            action = compiled.steps[0].action
            if action.get("kind") != "click_link" or action.get("target_page_id") != str(target_page.id):
                skipped += 1
                continue
            evidence: list[dict[str, Any]] = [
                _page_evidence(page.id),
                _element_evidence(page.id, element.id),
                _page_evidence(target_page.id),
            ]
            graph_ref = _graph_link_evidence(edges, nodes_by_id, page, target_page, element)
            if graph_ref:
                evidence.append(graph_ref)
            step = _step_values(
                ordinal=0,
                step_type="page_link",
                provenance="suggested",
                instruction=compiled.steps[0].instruction,
                page=page,
                target_page=target_page,
                control_name=label,
                evidence=evidence,
                expected_state={"target_page_id": str(target_page.id), "target_url": target_url, "captured": True},
            )
            sources.append({
                "source_type": "page_link",
                "source_id": str(element.id),
                "provenance": "suggested",
                "name": f"Open {label.title()}",
                "description": "A safe internal link and its saved target page were found in the exploration. This link click was not observed.",
                "steps": [step],
                "evidence": evidence,
            })
    return sources, skipped


def _collect_observation_sources(
    exploration_id: UUID,
    pages: list[ExplorationPage],
    observations: list[InteractionObservation],
) -> tuple[list[dict[str, Any]], int]:
    output: list[dict[str, Any]] = []
    skipped = 0
    for observation in observations:
        try:
            validated = validate_interaction_observation(observation, exploration_id, pages)
        except InvalidInteractionObservation:
            skipped += 1
            continue
        before_state = validated.state(validated.before_value)
        after_state = validated.state(validated.observed_after_value)
        state_key = "selected" if observation.action_type == "tab" else "expanded"
        instruction = f"Observed {observation.action_type} interaction: {validated.name}."
        evidence = [
            {"source_type": "interaction_observation", "source_id": str(observation.id), "page_id": str(validated.page.id), "element_id": str(validated.element.id)},
            _page_evidence(validated.page.id),
            _element_evidence(validated.page.id, validated.element.id),
        ]
        step = _step_values(
            ordinal=0,
            step_type="interaction_transition",
            provenance="observed",
            instruction=instruction,
            page=validated.page,
            control_name=validated.name,
            evidence=evidence,
            observed_state={"before": before_state, "after": after_state, "attribute": validated.attribute},
            expected_state=None,
        )
        output.append({
            "source_type": "interaction_observation",
            "source_id": str(observation.id),
            "provenance": "observed",
            "name": f"Observed {observation.action_type}: {validated.name}"[:200],
            "description": "One isolated state transition was recorded. It is not evidence of a longer user journey or a recommended expected outcome.",
            "steps": [step],
            "evidence": evidence,
        })
    return output, skipped


def _collect_test_case_sources(
    session: Session,
    exploration: Exploration,
    pages: list[ExplorationPage],
    cases: list[TestCase],
) -> tuple[list[dict[str, Any]], int]:
    analysis = session.scalar(
        select(ExplorationAnalysis).where(ExplorationAnalysis.exploration_id == exploration.id)
    )
    output: list[dict[str, Any]] = []
    skipped = 0
    for case in cases:
        if case.deleted_at is not None or not case.is_current or not isinstance(case.definition, dict):
            continue
        if case.scenario_version_hash != _hash(case.definition):
            skipped += 1
            continue
        if case.source == "ai_generated":
            try:
                if (
                    analysis is None
                    or analysis.status != "completed"
                    or case.source_analysis_id != analysis.id
                    or case.analysis_version_hash != analysis_result_version_hash(analysis)
                ):
                    skipped += 1
                    continue
            except AnalysisNotReady:
                skipped += 1
                continue
        try:
            observations = load_interaction_observations(session, case.definition.get("scenario", {}))
            compiled = compile_test_case_definition(
                case.definition,
                pages,
                exploration.start_url,
                exploration_id=exploration.id,
                observations=observations,
                allow_observed_interactions=case.source == "user_created",
            )
        except (InvalidScenario, ValidationError, ValueError, KeyError):
            skipped += 1
            continue
        provenance = "suggested" if case.source == "ai_generated" else "user_authored"
        steps: list[dict[str, Any]] = []
        workflow_evidence: list[dict[str, Any]] = [{"source_type": "test_case", "source_id": str(case.id)}]
        definitions = case.definition.get("steps", [])
        for compiled_step, saved_step in zip(compiled.steps, definitions, strict=True):
            action = compiled_step.action
            try:
                page_id = UUID(str(action.get("page_id")))
            except (ValueError, TypeError):
                skipped += 1
                steps = []
                break
            page = next((item for item in pages if item.id == page_id), None)
            if page is None:
                skipped += 1
                steps = []
                break
            evidence = [_page_evidence(page.id), {"source_type": "test_case", "source_id": str(case.id)}]
            evidence_items = saved_step.get("expected_evidence", {}) if isinstance(saved_step, dict) else {}
            element_id_value = action.get("element_id")
            if element_id_value:
                try:
                    element_id = UUID(str(element_id_value))
                except (ValueError, TypeError):
                    skipped += 1
                    steps = []
                    break
                element = next((item for item in page.elements if item.id == element_id), None)
                if element is None:
                    skipped += 1
                    steps = []
                    break
                evidence.append(_element_evidence(page.id, element.id))
                workflow_evidence.append(_element_evidence(page.id, element.id))
            target_page = None
            if action.get("target_page_id"):
                try:
                    target_id = UUID(str(action["target_page_id"]))
                except (ValueError, TypeError):
                    skipped += 1
                    steps = []
                    break
                target_page = next((item for item in pages if item.id == target_id), None)
                if target_page is None:
                    skipped += 1
                    steps = []
                    break
                evidence.append(_page_evidence(target_page.id))
            observation_id = action.get("observation_id")
            step_provenance = "observed" if observation_id else provenance
            if observation_id:
                evidence.append({"source_type": "interaction_observation", "source_id": str(observation_id), "page_id": str(page.id), "element_id": str(action.get("element_id", ""))})
            workflow_evidence.extend(evidence)
            steps.append(_step_values(
                ordinal=compiled_step.ordinal,
                step_type=str(action.get("kind", "test_case_step")),
                provenance=step_provenance,
                instruction=compiled_step.instruction,
                page=page,
                target_page=target_page,
                control_name=action.get("link_name") or action.get("name"),
                evidence=evidence,
                expected_state=evidence_items if isinstance(evidence_items, dict) else None,
            ))
        if not steps:
            continue
        unique_evidence = list({json.dumps(item, sort_keys=True): item for item in workflow_evidence}.values())
        output.append({
            "source_type": "test_case",
            "source_id": str(case.id),
            "provenance": provenance,
            "name": case.name,
            "description": case.description or f"Ordered steps copied from the saved {case.source.replace('_', ' ')} test case. Execution status is not implied.",
            "steps": steps,
            "evidence": unique_evidence,
        })
    return output, skipped


def _persist_source(
    session: Session,
    exploration_id: UUID,
    project_id: UUID | None,
    source: dict[str, Any],
) -> tuple[str, bool]:
    source_type, source_id = source["source_type"], source["source_id"]
    identity_hash, workflow_id = _identity(exploration_id, source_type, source_id)
    source_hash = _hash({
        "source_type": source_type,
        "source_id": source_id,
        "provenance": source["provenance"],
        "steps": source["steps"],
        "evidence": source["evidence"],
    })
    workflow = session.scalar(
        select(Workflow).options(selectinload(Workflow.steps)).where(
            Workflow.exploration_id == exploration_id,
            Workflow.identity_hash == identity_hash,
        )
    )
    now = datetime.now(UTC)
    if workflow is None:
        workflow = Workflow(
            id=workflow_id,
            exploration_id=exploration_id,
            project_id=project_id,
            identity_hash=identity_hash,
            source_type=source_type,
            source_id=source_id,
            source_hash=source_hash,
            provenance=source["provenance"],
            status="active",
            name=source["name"][:200],
            description=source["description"][:4000],
            evidence=source["evidence"],
            reviewed=False,
        )
        session.add(workflow)
        session.flush()
        for ordinal, values in enumerate(source["steps"]):
            session.add(WorkflowStep(id=uuid5(workflow_id, f"step:{ordinal}"), workflow_id=workflow_id, **values))
        return "created", True
    workflow.project_id = project_id
    if workflow.source_hash == source_hash and workflow.status == "active":
        return "unchanged", False
    workflow.source_hash = source_hash
    workflow.provenance = source["provenance"]
    workflow.status = "active"
    workflow.evidence = source["evidence"]
    workflow.reviewed = False
    workflow.reviewed_at = None
    workflow.updated_at = now
    for step in list(workflow.steps):
        session.delete(step)
    session.flush()
    for ordinal, values in enumerate(source["steps"]):
        session.add(WorkflowStep(id=uuid5(workflow_id, f"step:{ordinal}"), workflow_id=workflow_id, **values))
    return "updated", True


def rebuild_exploration_workflows(session: Session, exploration_id: UUID) -> dict[str, int | UUID]:
    exploration = session.scalar(select(Exploration).where(Exploration.id == exploration_id).with_for_update())
    if exploration is None:
        raise WorkflowSourceNotFound("Exploration was not found.")
    if exploration.status != "completed":
        raise WorkflowRebuildUnavailable("Workflow discovery requires a completed exploration.")
    project_id = _project_id(session, exploration_id)
    if project_id is not None:
        if session.get(Project, project_id) is None:
            raise WorkflowRebuildUnavailable("The exploration project association is invalid.")
        metadata = session.get(ProjectMetadata, project_id)
        if metadata is not None and metadata.archived_at is not None:
            raise WorkflowRebuildUnavailable("Archived projects cannot rebuild workflows.")

    pages = list(session.scalars(
        select(ExplorationPage).options(selectinload(ExplorationPage.elements))
        .where(ExplorationPage.exploration_id == exploration_id)
        .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.id)
    ))
    edges = list(session.scalars(select(KnowledgeGraphEdge).where(KnowledgeGraphEdge.exploration_id == exploration_id)))
    nodes = list(session.scalars(select(KnowledgeGraphNode).where(KnowledgeGraphNode.exploration_id == exploration_id)))
    observations = list(session.scalars(
        select(InteractionObservation).where(InteractionObservation.exploration_id == exploration_id)
        .order_by(InteractionObservation.created_at, InteractionObservation.id)
    ))
    cases = list(session.scalars(
        select(TestCase).where(TestCase.exploration_id == exploration_id).order_by(TestCase.id)
    ))
    events = list(session.scalars(select(ExplorationEvent).where(ExplorationEvent.exploration_id == exploration_id)))

    link_sources, skipped_links = _collect_page_link_sources(exploration, pages, edges, nodes, events)
    observation_sources, skipped_observations = _collect_observation_sources(exploration_id, pages, observations)
    test_case_sources, skipped_cases = _collect_test_case_sources(session, exploration, pages, cases)
    generated_sources = link_sources + observation_sources + test_case_sources
    active_identities: set[str] = set()
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for source in generated_sources:
        identity_hash, _ = _identity(exploration_id, source["source_type"], source["source_id"])
        active_identities.add(identity_hash)
        action, _ = _persist_source(session, exploration_id, project_id, source)
        counts[action] += 1

    existing = list(session.scalars(select(Workflow).where(Workflow.exploration_id == exploration_id)))
    marked_stale = 0
    preserved_manual = 0
    for workflow in existing:
        workflow.project_id = project_id
        if workflow.source_type == "manual":
            preserved_manual += 1
            continue
        if workflow.identity_hash not in active_identities and workflow.status != "stale":
            workflow.status = "stale"
            workflow.updated_at = datetime.now(UTC)
            marked_stale += 1
    session.flush()
    return {
        "exploration_id": exploration_id,
        **counts,
        "marked_stale": marked_stale,
        "preserved_manual": preserved_manual,
        "skipped_invalid_sources": skipped_links + skipped_observations + skipped_cases,
    }


def list_exploration_workflows(
    session: Session,
    exploration_id: UUID,
    *,
    search: str | None = None,
    provenance: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Workflow], int]:
    statement = select(Workflow).options(selectinload(Workflow.steps)).where(Workflow.exploration_id == exploration_id)
    count_statement = select(Workflow.id).where(Workflow.exploration_id == exploration_id)
    if search and search.strip():
        statement = statement.where(Workflow.name.ilike(f"%{search.strip()}%"))
        count_statement = count_statement.where(Workflow.name.ilike(f"%{search.strip()}%"))
    if provenance:
        statement = statement.where(Workflow.provenance == provenance)
        count_statement = count_statement.where(Workflow.provenance == provenance)
    if status in {"active", "stale"}:
        statement = statement.where(Workflow.status == status)
        count_statement = count_statement.where(Workflow.status == status)
    elif status == "reviewed":
        statement = statement.where(Workflow.reviewed.is_(True))
        count_statement = count_statement.where(Workflow.reviewed.is_(True))
    elif status == "unreviewed":
        statement = statement.where(Workflow.reviewed.is_(False))
        count_statement = count_statement.where(Workflow.reviewed.is_(False))
    total = session.scalar(select(func.count()).select_from(count_statement.subquery())) or 0
    items = list(session.scalars(statement.order_by(Workflow.created_at.desc(), Workflow.id).limit(limit).offset(offset)))
    return items, int(total)


def list_project_workflows(
    session: Session,
    project_id: UUID,
    *,
    search: str | None = None,
    provenance: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Workflow], int]:
    statement = select(Workflow).options(selectinload(Workflow.steps)).join(
        ProjectExploration, ProjectExploration.exploration_id == Workflow.exploration_id
    ).where(ProjectExploration.project_id == project_id)
    count_statement = select(Workflow.id).join(
        ProjectExploration, ProjectExploration.exploration_id == Workflow.exploration_id
    ).where(ProjectExploration.project_id == project_id)
    if search and search.strip():
        statement = statement.where(Workflow.name.ilike(f"%{search.strip()}%"))
        count_statement = count_statement.where(Workflow.name.ilike(f"%{search.strip()}%"))
    if provenance:
        statement = statement.where(Workflow.provenance == provenance)
        count_statement = count_statement.where(Workflow.provenance == provenance)
    if status in {"active", "stale"}:
        statement = statement.where(Workflow.status == status)
        count_statement = count_statement.where(Workflow.status == status)
    elif status == "reviewed":
        statement = statement.where(Workflow.reviewed.is_(True))
        count_statement = count_statement.where(Workflow.reviewed.is_(True))
    elif status == "unreviewed":
        statement = statement.where(Workflow.reviewed.is_(False))
        count_statement = count_statement.where(Workflow.reviewed.is_(False))
    total = session.scalar(select(func.count()).select_from(count_statement.subquery())) or 0
    items = list(session.scalars(statement.order_by(Workflow.created_at.desc(), Workflow.id).limit(limit).offset(offset)))
    return items, int(total)


def get_workflow(session: Session, exploration_id: UUID, workflow_id: UUID) -> Workflow:
    workflow = session.scalar(
        select(Workflow).options(selectinload(Workflow.steps)).where(
            Workflow.id == workflow_id, Workflow.exploration_id == exploration_id
        )
    )
    if workflow is None:
        raise WorkflowNotFound("Workflow was not found for this exploration.")
    return workflow


def update_workflow(workflow: Workflow, *, name: str | None, description: str | None) -> Workflow:
    if name is not None:
        workflow.name = name
    if description is not None:
        workflow.description = description
    workflow.updated_at = datetime.now(UTC)
    return workflow


def mark_workflow_reviewed(workflow: Workflow) -> Workflow:
    workflow.reviewed = True
    workflow.reviewed_at = datetime.now(UTC)
    workflow.updated_at = workflow.reviewed_at
    return workflow

