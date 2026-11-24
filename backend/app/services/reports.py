from __future__ import annotations

import html
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from playwright.async_api import async_playwright
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

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
from app.models.report import GeneratedReport
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.models.workflow import Workflow, WorkflowStep
from app.schemas.analysis import AnalysisResult

logger = logging.getLogger("software_intelligence.reports")

_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|credential)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_LONG_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{48,}$")

class ReportSourceNotFound(LookupError):
    pass


class ReportGenerationFailed(RuntimeError):
    def __init__(self, report_id: UUID) -> None:
        super().__init__("The report could not be generated.")
        self.report_id = report_id


class ReportNotFound(LookupError):
    pass


class ReportNotReady(RuntimeError):
    pass


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, limit: int = 1000) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", " ").strip()
    value = _KEY_VALUE_SECRET.sub(r"\1=[redacted]", value)
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _JWT.sub("[redacted token]", value)
    value = _EMAIL.sub("[redacted email]", value)
    return value[:limit]


def _safe_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.encode("idna").decode("ascii")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        path = "/".join(
            "[redacted]" if _LONG_PATH_SEGMENT.match(segment) or re.match(r"(?i)^(token|session|secret|auth|password|reset|api[-_]?key)$", segment) else segment
            for segment in parsed.path.split("/")
        )
        # Query strings and URL credentials can contain keys or session tokens.
        return urlunsplit((parsed.scheme, netloc, path, "", ""))
    except (UnicodeError, ValueError):
        return None


def _failure_summary(message: str | None) -> str | None:
    if not message:
        return None
    lowered = message.casefold()
    if "timeout" in lowered or "timed out" in lowered:
        return "Timed out"
    if "selector" in lowered or "locator" in lowered or "assert" in lowered:
        return "Element or assertion did not match"
    if "navigation" in lowered or "network" in lowered or "http" in lowered:
        return "Navigation or network operation failed"
    return "Execution failed; internal details are omitted"


def _status_counts(rows: list[Any], field: str = "status") -> dict[str, int]:
    return dict(sorted(Counter(str(getattr(row, field, "unknown")) for row in rows).items()))


def _json_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _valid_reference_ids(
    evidence: Any,
    page_ids: set[str],
    element_page_by_id: dict[str, str],
    other_ids: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(evidence, list):
        return [], False
    result: list[dict[str, Any]] = []
    all_valid = bool(evidence)
    for item in evidence:
        if not isinstance(item, dict):
            all_valid = False
            continue
        source_type = str(item.get("source_type") or "")
        source_id = str(item.get("source_id") or "")
        page_id = str(item.get("page_id") or (source_id if source_type == "page" else ""))
        element_id = str(item.get("element_id") or (source_id if source_type == "element" else ""))
        valid = True
        if source_type == "page":
            valid = page_id in page_ids
        elif source_type == "element":
            valid = element_id in element_page_by_id and (
                not page_id or element_page_by_id.get(element_id) == page_id
            )
        elif source_type in (other_ids or {}):
            valid = source_id in (other_ids or {})[source_type]
        else:
            valid = False
        if not valid:
            all_valid = False
            continue
        safe: dict[str, Any] = {}
        for key in ("source_type", "source_id", "page_id", "element_id", "target_page_id"):
            value = item.get(key)
            if value is not None and isinstance(value, (str, UUID)):
                safe[key] = str(value)
        if safe:
            result.append(safe)
    return result, all_valid


def _analysis_section(
    analysis: ExplorationAnalysis | None,
    pages: list[ExplorationPage],
    elements: list[dict[str, Any]],
    limitations: list[str],
) -> dict[str, Any]:
    if analysis is None:
        limitations.append("No persisted AI analysis is available for this exploration.")
        return {"status": "not_started", "analysis_id": None, "result": None}
    if analysis.status != "completed" or not isinstance(analysis.result, dict):
        limitations.append(f"AI analysis is {analysis.status}; completed findings are unavailable.")
        return {
            "status": analysis.status,
            "analysis_id": str(analysis.id),
            "model": _safe_text(analysis.model, 200),
            "completed_at": _utc(analysis.completed_at),
            "result": None,
        }
    try:
        parsed = AnalysisResult.model_validate(analysis.result)
    except ValidationError:
        limitations.append("The saved AI analysis result does not match its validated schema.")
        return {
            "status": "invalid_saved_result",
            "analysis_id": str(analysis.id),
            "model": _safe_text(analysis.model, 200),
            "completed_at": _utc(analysis.completed_at),
            "result": None,
        }

    page_ids = {str(page.id) for page in pages}
    element_page_by_id = {str(item["id"]): str(item["page_id"]) for item in elements}
    stale_evidence = 0

    def evidence(items: list[Any]) -> list[dict[str, Any]]:
        nonlocal stale_evidence
        output: list[dict[str, Any]] = []
        for entry in items:
            raw = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else entry
            page_id = str(raw.get("page_id", ""))
            element_ids = [str(value) for value in raw.get("element_ids", [])]
            valid_elements = [
                item_id for item_id in element_ids
                if item_id in element_page_by_id and element_page_by_id[item_id] == page_id
            ]
            valid = page_id in page_ids and len(valid_elements) == len(element_ids)
            if not valid:
                stale_evidence += 1
            if page_id in page_ids:
                output.append({"page_id": page_id, "element_ids": valid_elements, "valid": valid})
        return output

    purpose = parsed.website_purpose
    result: dict[str, Any] = {
        "provenance": "ai_interpretation",
        "website_purpose": {
            "interpretation": _safe_text(purpose.interpretation),
            "evidence": evidence(purpose.evidence),
        },
        "page_summaries": [],
        "main_navigation": [],
        "important_interactive_elements": [],
        "forms_and_fields": [],
        "potential_workflows": [],
        "warnings": [
            {"collection": warning.collection, "discarded_count": warning.discarded_count}
            for warning in parsed.warnings[:3]
        ],
    }
    for item in parsed.page_summaries:
        page_id = str(item.page_id)
        if page_id not in page_ids:
            stale_evidence += 1
            continue
        result["page_summaries"].append({
            "page_id": page_id,
            "interpretation": _safe_text(item.interpretation),
            "evidence": evidence(item.evidence),
        })
    def analysis_evidence(item: Any) -> dict[str, Any]:
        nonlocal stale_evidence
        page_id = str(item.page_id)
        element_ids = [str(value) for value in item.element_ids]
        valid = page_id in page_ids and all(
            element_id in element_page_by_id and element_page_by_id[element_id] == page_id
            for element_id in element_ids
        )
        if not valid:
            stale_evidence += 1
        return {
            "page_id": page_id if page_id in page_ids else None,
            "element_ids": [value for value in element_ids if value in element_page_by_id and element_page_by_id[value] == page_id],
            "valid": valid,
        }

    for item in parsed.main_navigation:
        item_evidence = analysis_evidence(item.evidence)
        target_page_id = str(item.target_page_id) if item.target_page_id else None
        if not item_evidence["valid"] or (target_page_id is not None and target_page_id not in page_ids):
            stale_evidence += 1
            continue
        result["main_navigation"].append({
            "interpretation": _safe_text(item.interpretation),
            "evidence": item_evidence,
            "target_page_id": target_page_id,
        })
    for item in parsed.important_interactive_elements:
        item_evidence = analysis_evidence(item.evidence)
        if not item_evidence["valid"]:
            stale_evidence += 1
            continue
        result["important_interactive_elements"].append({
            "interpretation": _safe_text(item.interpretation), "evidence": item_evidence,
        })
    for item in parsed.forms_and_fields:
        page_id = str(item.page_id)
        if page_id not in page_ids:
            stale_evidence += 1
            continue
        form_ids = [str(value) for value in item.form_element_ids if element_page_by_id.get(str(value)) == page_id]
        field_ids = [str(value) for value in item.field_element_ids if element_page_by_id.get(str(value)) == page_id]
        if len(form_ids) != len(item.form_element_ids) or len(field_ids) != len(item.field_element_ids):
            stale_evidence += 1
        result["forms_and_fields"].append({
            "page_id": page_id, "form_element_ids": form_ids, "field_element_ids": field_ids,
            "relationship_note": item.relationship_note,
        })
    for item in parsed.potential_workflows:
        relevant = [str(value) for value in item.relevant_page_ids if str(value) in page_ids]
        if len(relevant) != len(item.relevant_page_ids):
            stale_evidence += 1
            continue
        steps = []
        for step in item.steps:
            safe_evidence = evidence(step.evidence)
            if safe_evidence:
                steps.append({"instruction": _safe_text(step.instruction), "evidence": safe_evidence})
        result["potential_workflows"].append({
            "provenance": "suggested",
            "executed": False,
            "title": _safe_text(item.title, 200),
            "objective": _safe_text(item.objective),
            "relevant_page_ids": relevant,
            "steps": steps,
            "expected_outcome": _safe_text(item.expected_outcome),
        })
    if stale_evidence:
        limitations.append(f"{stale_evidence} AI finding(s) referenced missing or stale page/element evidence and were excluded or marked invalid.")
    return {
        "status": "completed",
        "analysis_id": str(analysis.id),
        "model": _safe_text(analysis.model, 200),
        "completed_at": _utc(analysis.completed_at),
        "result": result,
    }


def _observation_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    target = value.get("target") if isinstance(value.get("target"), dict) else {}
    visible = value.get("visible_changes") if isinstance(value.get("visible_changes"), dict) else {}
    return {
        "url": _safe_url(value.get("url")),
        "title": _safe_text(value.get("title"), 160),
        "target": {
            "tag_name": _safe_text(target.get("tag_name"), 40),
            "role": _safe_text(target.get("role"), 80),
            "name": _safe_text(target.get("name"), 200),
            "expanded": target.get("expanded") if isinstance(target.get("expanded"), bool) else None,
            "selected": target.get("selected") if isinstance(target.get("selected"), bool) else None,
        },
        "visible_change_counts": {
            key: len(visible.get(key, [])) if isinstance(visible.get(key), list) else 0
            for key in ("added", "removed", "state_changes")
        },
    }


def _workflow_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = ("expanded", "selected", "aria_expanded", "aria_selected", "relationship")
    return {
        key: item for key in allowed
        if isinstance((item := value.get(key)), (bool, str, int))
    }


def _make_snapshot(
    session: Session,
    report: GeneratedReport,
    *,
    project: Project | None = None,
    exploration_id: UUID | None = None,
) -> dict[str, Any]:
    limitations: list[str] = []
    project_metadata: ProjectMetadata | None = None
    associations: list[ProjectExploration] = []
    if project is not None:
        project_metadata = session.get(ProjectMetadata, project.id)
        associations = list(session.scalars(
            select(ProjectExploration).where(ProjectExploration.project_id == project.id)
            .order_by(ProjectExploration.created_at, ProjectExploration.exploration_id)
        ))
        requested_ids = [row.exploration_id for row in associations]
        explorations = list(session.scalars(
            select(Exploration).where(Exploration.id.in_(requested_ids)).order_by(Exploration.created_at, Exploration.id)
        )) if requested_ids else []
        existing = {item.id for item in explorations}
        invalid_associations = [row.exploration_id for row in associations if row.exploration_id not in existing]
        if invalid_associations:
            limitations.append(f"{len(invalid_associations)} project association(s) reference an unavailable exploration and were excluded.")
    else:
        exploration = session.get(Exploration, exploration_id) if exploration_id is not None else None
        if exploration is None:
            raise ReportSourceNotFound("Exploration was not found.")
        explorations = [exploration]
        linked_project_id = session.scalar(
            select(ProjectExploration.project_id).where(ProjectExploration.exploration_id == exploration.id)
        )
        if linked_project_id is not None:
            project = session.get(Project, linked_project_id)
            if project is None:
                limitations.append("The exploration has an invalid project association.")
            else:
                project_metadata = session.get(ProjectMetadata, project.id)

    exploration_ids = [item.id for item in explorations]
    pages = list(session.execute(
        select(ExplorationPage.id, ExplorationPage.exploration_id, ExplorationPage.url,
               ExplorationPage.title, ExplorationPage.depth, ExplorationPage.http_status,
               ExplorationPage.element_count, ExplorationPage.discovered_at)
        .where(ExplorationPage.exploration_id.in_(exploration_ids))
        .order_by(ExplorationPage.exploration_id, ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.id)
    )) if exploration_ids else []
    page_ids = [item.id for item in pages]
    elements = list(session.execute(
        select(
            ObservedElement.id, ObservedElement.page_id, ObservedElement.ordinal,
            ObservedElement.kind, ObservedElement.tag_name, ObservedElement.role,
            ObservedElement.accessible_name, ObservedElement.label,
        ).where(ObservedElement.page_id.in_(page_ids))
        .order_by(ObservedElement.page_id, ObservedElement.ordinal, ObservedElement.id)
    )) if page_ids else []
    events = list(session.execute(
        select(ExplorationEvent.id, ExplorationEvent.exploration_id, ExplorationEvent.sequence,
               ExplorationEvent.event_type, ExplorationEvent.url, ExplorationEvent.created_at)
        .where(ExplorationEvent.exploration_id.in_(exploration_ids))
        .order_by(ExplorationEvent.exploration_id, ExplorationEvent.sequence)
    )) if exploration_ids else []
    analyses = list(session.scalars(
        select(ExplorationAnalysis).where(ExplorationAnalysis.exploration_id.in_(exploration_ids))
    )) if exploration_ids else []
    analysis_by_exploration = {row.exploration_id: row for row in analyses}
    observations = list(session.execute(
        select(
            InteractionObservation.id, InteractionObservation.exploration_id, InteractionObservation.page_id,
            InteractionObservation.element_ordinal, InteractionObservation.action_type,
            InteractionObservation.outcome, InteractionObservation.reason,
            InteractionObservation.before_evidence, InteractionObservation.after_evidence,
            InteractionObservation.duration_ms, InteractionObservation.created_at,
        ).where(InteractionObservation.exploration_id.in_(exploration_ids))
        .order_by(InteractionObservation.exploration_id, InteractionObservation.created_at, InteractionObservation.id)
    )) if exploration_ids else []
    graph_nodes = list(session.execute(
        select(KnowledgeGraphNode.id, KnowledgeGraphNode.exploration_id, KnowledgeGraphNode.node_type,
               KnowledgeGraphNode.source_key, KnowledgeGraphNode.label, KnowledgeGraphNode.page_id,
               KnowledgeGraphNode.provenance, KnowledgeGraphNode.evidence)
        .where(KnowledgeGraphNode.exploration_id.in_(exploration_ids))
        .order_by(KnowledgeGraphNode.exploration_id, KnowledgeGraphNode.node_type, KnowledgeGraphNode.id)
    )) if exploration_ids else []
    graph_edges = list(session.execute(
        select(KnowledgeGraphEdge.id, KnowledgeGraphEdge.exploration_id, KnowledgeGraphEdge.source_node_id,
               KnowledgeGraphEdge.target_node_id, KnowledgeGraphEdge.edge_type,
               KnowledgeGraphEdge.provenance, KnowledgeGraphEdge.evidence)
        .where(KnowledgeGraphEdge.exploration_id.in_(exploration_ids))
        .order_by(KnowledgeGraphEdge.exploration_id, KnowledgeGraphEdge.edge_type, KnowledgeGraphEdge.id)
    )) if exploration_ids else []
    workflows = list(session.scalars(
        select(Workflow).where(Workflow.exploration_id.in_(exploration_ids))
        .order_by(Workflow.exploration_id, Workflow.provenance, Workflow.created_at, Workflow.id)
    )) if exploration_ids else []
    workflow_ids = [row.id for row in workflows]
    workflow_steps = list(session.scalars(
        select(WorkflowStep).where(WorkflowStep.workflow_id.in_(workflow_ids))
        .order_by(WorkflowStep.workflow_id, WorkflowStep.ordinal, WorkflowStep.id)
    )) if workflow_ids else []
    steps_by_workflow: dict[UUID, list[WorkflowStep]] = defaultdict(list)
    for row in workflow_steps:
        steps_by_workflow[row.workflow_id].append(row)
    test_cases = list(session.execute(
        select(TestCase.id, TestCase.exploration_id, TestCase.source, TestCase.is_current,
               TestCase.deleted_at, TestCase.name, TestCase.description, TestCase.source_analysis_id, TestCase.created_at,
               TestCase.updated_at)
        .where(TestCase.exploration_id.in_(exploration_ids))
        .order_by(TestCase.exploration_id, TestCase.source, TestCase.created_at, TestCase.id)
    )) if exploration_ids else []

    run_statement = select(
        TestRun.id, TestRun.exploration_id, TestRun.test_case_id, TestRun.status,
        TestRun.created_at, TestRun.started_at, TestRun.completed_at, TestRun.error_message,
    ).where(TestRun.exploration_id.in_(exploration_ids)) if exploration_ids else None
    run_from = report.filters.get("run_created_from") if report.filters else None
    run_to = report.filters.get("run_created_to") if report.filters else None
    if run_statement is not None:
        if run_from:
            run_statement = run_statement.where(TestRun.created_at >= datetime.fromisoformat(run_from.replace("Z", "+00:00")))
        if run_to:
            run_statement = run_statement.where(TestRun.created_at <= datetime.fromisoformat(run_to.replace("Z", "+00:00")))
        runs = list(session.execute(run_statement.order_by(TestRun.exploration_id, TestRun.created_at, TestRun.id)))
    else:
        runs = []
    run_ids = [row.id for row in runs]
    run_steps = list(session.execute(
        select(TestRunStep.id, TestRunStep.test_run_id, TestRunStep.ordinal, TestRunStep.status,
               TestRunStep.duration_ms, TestRunStep.error_message)
        .where(TestRunStep.test_run_id.in_(run_ids))
        .order_by(TestRunStep.test_run_id, TestRunStep.ordinal, TestRunStep.id)
    )) if run_ids else []
    run_steps_by_run: dict[UUID, list[TestRunStep]] = defaultdict(list)
    for row in run_steps:
        run_steps_by_run[row.test_run_id].append(row)

    element_rows = [
        {
            "id": str(row.id), "page_id": str(row.page_id), "ordinal": row.ordinal,
            "kind": _safe_text(row.kind, 40), "tag_name": _safe_text(row.tag_name, 40),
            "role": _safe_text(row.role, 80),
            "name": _safe_text(row.accessible_name or row.label, 200),
        }
        for row in elements
    ]
    elements_by_page: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in element_rows:
        elements_by_page[UUID(row["page_id"])].append(row)
    element_page_by_id = {row["id"]: row["page_id"] for row in element_rows}
    page_id_strings = {str(page.id) for page in pages}
    node_by_id = {row.id: row for row in graph_nodes}
    reference_ids = {
        "exploration": {str(value) for value in exploration_ids},
        "analysis": {str(row.id) for row in analyses},
        "interaction_observation": {str(row.id) for row in observations},
        "workflow": {str(row.id) for row in workflows},
        "test_case": {str(row.id) for row in test_cases},
    }

    nodes_out: list[dict[str, Any]] = []
    for row in graph_nodes:
        evidence, valid = _valid_reference_ids(row.evidence, page_id_strings, element_page_by_id, reference_ids)
        if not valid:
            limitations.append(f"Knowledge graph node {row.id} has missing or stale source evidence.")
        nodes_out.append({
            "id": str(row.id), "exploration_id": str(row.exploration_id), "node_type": row.node_type,
            "source_key": _safe_text(row.source_key, 300), "label": _safe_text(row.label, 300),
            "page_id": str(row.page_id) if row.page_id else None,
            "provenance": row.provenance, "evidence": evidence,
            "evidence_status": "valid" if valid else "partial",
        })
    edges_out: list[dict[str, Any]] = []
    for row in graph_edges:
        evidence, valid = _valid_reference_ids(row.evidence, page_id_strings, element_page_by_id, reference_ids)
        endpoints_valid = (
            row.source_node_id in node_by_id and row.target_node_id in node_by_id
            and node_by_id[row.source_node_id].exploration_id == row.exploration_id
            and node_by_id[row.target_node_id].exploration_id == row.exploration_id
        )
        valid = valid and endpoints_valid
        if not valid:
            limitations.append(f"Knowledge graph edge {row.id} has a missing endpoint or stale evidence.")
        edges_out.append({
            "id": str(row.id), "exploration_id": str(row.exploration_id),
            "source_node_id": str(row.source_node_id), "target_node_id": str(row.target_node_id),
            "edge_type": row.edge_type, "provenance": row.provenance,
            "evidence": evidence, "evidence_status": "valid" if valid else "partial",
        })

    nodes_by_exploration: dict[UUID, list[Any]] = defaultdict(list)
    for row in graph_nodes:
        nodes_by_exploration[row.exploration_id].append(row)
    edges_by_exploration: dict[UUID, list[Any]] = defaultdict(list)
    for row in graph_edges:
        edges_by_exploration[row.exploration_id].append(row)
    observed_link_edges_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in graph_edges:
        source = node_by_id.get(row.source_node_id)
        target = node_by_id.get(row.target_node_id)
        link_evidence, link_evidence_valid = _valid_reference_ids(
            row.evidence, page_id_strings, element_page_by_id, reference_ids
        )
        if (
            row.edge_type == "links_to" and row.provenance == "verified" and source and target
            and link_evidence_valid
            and source.exploration_id == row.exploration_id and target.exploration_id == row.exploration_id
        ):
            observed_link_edges_by_exploration[row.exploration_id].append({
                "edge_id": str(row.id), "from_page_node_id": str(source.id),
                "to_page_node_id": str(target.id), "evidence": link_evidence,
            })

    workflows_out: list[dict[str, Any]] = []
    workflows_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in workflows:
        workflow_evidence, workflow_evidence_valid = _valid_reference_ids(
            row.evidence, page_id_strings, element_page_by_id, reference_ids
        )
        if not workflow_evidence_valid:
            limitations.append(f"Workflow {row.id} has missing or stale evidence references.")
        safe_steps = []
        for step in steps_by_workflow.get(row.id, []):
            step_evidence, step_evidence_valid = _valid_reference_ids(
                step.evidence, page_id_strings, element_page_by_id, reference_ids
            )
            if not step_evidence_valid:
                limitations.append(f"Workflow step {step.id} has missing or stale evidence references.")
            safe_steps.append({
                "id": str(step.id), "ordinal": step.ordinal, "step_type": step.step_type,
                "provenance": step.provenance, "instruction": _safe_text(step.instruction, 1000),
                "page_id": str(step.page_id), "target_page_id": str(step.target_page_id) if step.target_page_id else None,
                "page_url": _safe_url(step.page_url), "page_title": _safe_text(step.page_title, 300),
                "control_name": _safe_text(step.control_name, 200),
                "observed_state": _workflow_state(step.observed_state),
                "expected_state": _workflow_state(step.expected_state),
                "evidence": step_evidence,
                "evidence_status": "valid" if step_evidence_valid else "stale",
            })
        item = {
            "id": str(row.id), "exploration_id": str(row.exploration_id),
            "source_type": row.source_type, "source_id": _safe_text(row.source_id, 64),
            "provenance": row.provenance, "status": row.status, "reviewed": row.reviewed,
            "name": _safe_text(row.name, 200), "description": _safe_text(row.description, 1000),
            "created_at": _utc(row.created_at), "updated_at": _utc(row.updated_at),
            "steps": safe_steps,
            "evidence": workflow_evidence,
            "evidence_status": "valid" if workflow_evidence_valid else "stale",
        }
        workflows_out.append(item)
        workflows_by_exploration[row.exploration_id].append(item)

    runs_by_case: dict[UUID, list[Any]] = defaultdict(list)
    runs_by_exploration: dict[UUID, list[Any]] = defaultdict(list)
    cases_by_id = {row.id: row for row in test_cases}
    for row in runs:
        if row.test_case_id is not None:
            linked_case = cases_by_id.get(row.test_case_id)
            if linked_case is not None and linked_case.exploration_id == row.exploration_id:
                runs_by_case[row.test_case_id].append(row)
            else:
                limitations.append(f"Test run {row.id} has a missing or mismatched test-case association.")
        runs_by_exploration[row.exploration_id].append(row)
    cases_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    case_execution_counts: Counter[str] = Counter({
        "passed": 0, "failed": 0, "cancelled": 0, "pending": 0, "running": 0, "never_executed": 0,
    })
    for row in test_cases:
        case_status = "deleted" if row.deleted_at is not None else "active" if row.is_current else "superseded"
        case_runs = runs_by_case.get(row.id, [])
        latest = max(case_runs, key=lambda value: (value.created_at, str(value.id))) if case_runs else None
        execution_status = latest.status if latest is not None else "never_executed"
        case_execution_counts[execution_status] += 1
        linked_analysis = analysis_by_exploration.get(row.exploration_id)
        analysis_evidence_valid = row.source_analysis_id is None or (
            linked_analysis is not None and row.source_analysis_id == linked_analysis.id
        )
        if not analysis_evidence_valid:
            limitations.append(f"Test case {row.id} references an unavailable analysis record.")
        item = {
            "id": str(row.id), "exploration_id": str(row.exploration_id), "source": row.source,
            "status": case_status, "latest_execution_status": execution_status,
            "source_analysis_id": str(row.source_analysis_id) if row.source_analysis_id else None,
            "source_analysis_status": "valid" if analysis_evidence_valid else "stale",
            "name": _safe_text(row.name, 200), "description": _safe_text(row.description, 500),
            "created_at": _utc(row.created_at), "updated_at": _utc(row.updated_at),
            "run_count_in_report": len(case_runs),
        }
        cases_by_exploration[row.exploration_id].append(item)

    runs_out: list[dict[str, Any]] = []
    for row in runs:
        steps = run_steps_by_run.get(row.id, [])
        linked_case = cases_by_id.get(row.test_case_id) if row.test_case_id is not None else None
        case_association_status = (
            "unlinked_legacy_run" if row.test_case_id is None
            else "valid" if linked_case is not None and linked_case.exploration_id == row.exploration_id
            else "invalid"
        )
        runs_out.append({
            "id": str(row.id), "exploration_id": str(row.exploration_id),
            "test_case_id": str(row.test_case_id) if row.test_case_id else None,
            "test_case_association_status": case_association_status,
            "status": row.status, "created_at": _utc(row.created_at),
            "started_at": _utc(row.started_at), "completed_at": _utc(row.completed_at),
            "failure_summary": _failure_summary(row.error_message),
            "step_counts": _status_counts(steps),
            "steps": [
                {
                    "id": str(step.id), "ordinal": step.ordinal, "status": step.status,
                    "duration_ms": step.duration_ms,
                    "failure_summary": _failure_summary(step.error_message),
                }
                for step in steps
            ],
        })

    observations_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    known_page_ids = {str(value) for value in page_ids}
    known_page_ordinals = {(str(row.page_id), row.ordinal) for row in elements}
    for row in observations:
        before = _json_mapping(row.before_evidence)
        target = before.get("target") if isinstance(before.get("target"), dict) else {}
        observation_evidence_valid = (
            str(row.page_id) in known_page_ids
            and (str(row.page_id), row.element_ordinal) in known_page_ordinals
        )
        if not observation_evidence_valid:
            limitations.append(f"Interaction observation {row.id} references a missing page element.")
        observations_by_exploration[row.exploration_id].append({
            "id": str(row.id), "page_id": str(row.page_id), "element_ordinal": row.element_ordinal,
            "interaction_type": row.action_type, "outcome": row.outcome,
            "evidence_status": "valid" if observation_evidence_valid else "stale",
            "reason_code": _safe_text(row.reason, 160), "duration_ms": row.duration_ms,
            "created_at": _utc(row.created_at),
            "control": {
                "role": _safe_text(target.get("role"), 80), "name": _safe_text(target.get("name"), 200),
                "tag_name": _safe_text(target.get("tag_name"), 40),
            },
            "before": _observation_state(row.before_evidence),
            "after": _observation_state(row.after_evidence),
        })

    events_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_exploration[row.exploration_id].append({
            "id": str(row.id), "sequence": row.sequence, "event_type": row.event_type,
            "url": _safe_url(row.url), "created_at": _utc(row.created_at),
        })

    pages_out_by_exploration: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for page in pages:
        page_elements = elements_by_page.get(page.id, [])
        pages_out_by_exploration[page.exploration_id].append({
            "id": str(page.id), "url": _safe_url(page.url), "title": _safe_text(page.title, 300),
            "http_status": page.http_status, "depth": page.depth,
            "discovered_at": _utc(page.discovered_at), "element_count_reported": page.element_count,
            "element_count_persisted": len(page_elements),
            "elements": page_elements,
        })

    exploration_rows: list[dict[str, Any]] = []
    sections_by_exploration: list[dict[str, Any]] = []
    analysis_status_counts: Counter[str] = Counter()
    for item in explorations:
        item_pages = pages_out_by_exploration.get(item.id, [])
        item_elements = [element for page in item_pages for element in page["elements"]]
        item_workflows = workflows_by_exploration.get(item.id, [])
        item_cases = cases_by_exploration.get(item.id, [])
        item_runs = runs_by_exploration.get(item.id, [])
        analysis = _analysis_section(analysis_by_exploration.get(item.id), [page for page in pages if page.exploration_id == item.id], item_elements, limitations)
        analysis_status_counts[analysis["status"]] += 1
        link_edges = observed_link_edges_by_exploration.get(item.id, [])
        form_rows = [row for row in item_elements if row["kind"] == "form" or row["tag_name"] == "form"]
        field_rows = [row for row in item_elements if row["tag_name"] in {"input", "select", "textarea"}]
        if field_rows and form_rows:
            limitations.append(f"Exploration {item.id} has form fields without persisted form-parent IDs; fields are shown at page level.")
        if item_pages and not link_edges:
            limitations.append(f"No persisted verified navigation edges are available for exploration {item.id}.")
        if not item_workflows:
            limitations.append(f"No persisted workflows are available for exploration {item.id}; no user journey is inferred.")
        if not nodes_by_exploration.get(item.id):
            graph_status = "not_built"
        else:
            graph_status = "available"
        if not item_pages:
            limitations.append(f"Exploration {item.id} has no persisted page records.")
        exploration_rows.append({
            "id": str(item.id), "target_url": _safe_url(item.start_url), "status": item.status,
            "current_url": _safe_url(item.current_url), "created_at": _utc(item.created_at),
            "started_at": _utc(item.started_at), "completed_at": _utc(item.completed_at),
            "duration_seconds": max(0, int((item.completed_at - item.started_at).total_seconds()))
            if item.started_at and item.completed_at else None,
            "max_pages": item.max_pages, "max_depth": item.max_depth,
            "stored_pages_discovered": item.pages_discovered,
            "stored_elements_discovered": item.elements_discovered,
            "stored_event_count": item.event_count,
            "persisted_page_rows": len(item_pages), "persisted_element_rows": len(item_elements),
            "persisted_event_rows": len(events_by_exploration.get(item.id, [])),
            "error_present": bool(item.error_message),
        })
        case_groups: dict[str, dict[str, list[dict[str, Any]]]] = {
            state: {source: [] for source in ("ai_generated", "user_created")}
            for state in ("active", "superseded", "deleted")
        }
        for case in item_cases:
            case_groups[case["status"]][case["source"]].append(case)
        run_rows = [row for row in runs_out if row["exploration_id"] == str(item.id)]
        run_status_counts = {
            state: sum(1 for row in run_rows if row["status"] == state)
            for state in ("pending", "running", "passed", "failed", "cancelled")
        }
        page_id_index = {str(page.id): page for page in pages if page.exploration_id == item.id}
        page_nav = []
        for edge in link_edges:
            source_node = node_by_id.get(UUID(edge["from_page_node_id"]))
            target_node = node_by_id.get(UUID(edge["to_page_node_id"]))
            if source_node is None or target_node is None:
                continue
            source_page_id = source_node.source_key
            target_page_id = target_node.source_key
            if source_page_id in page_id_index and target_page_id in page_id_index:
                page_nav.append({
                    "source_page_id": source_page_id, "target_page_id": target_page_id,
                    "edge_id": edge["edge_id"], "provenance": "verified",
                    "evidence": edge["evidence"],
                })
        sections_by_exploration.append({
            "exploration_id": str(item.id),
            "pages_and_navigation": {
                "status": "available" if item_pages else "empty",
                "pages": item_pages, "verified_navigation_links": page_nav,
            },
            "ui_elements_and_forms": {
                "persisted_element_count": len(item_elements),
                "forms": [row for row in item_elements if row["kind"] == "form" or row["tag_name"] == "form"],
                "fields_page_level_only": field_rows,
                "form_parent_relationships_persisted": False,
            },
            "interaction_observations": {
                "count": len(observations_by_exploration.get(item.id, [])),
                "items": observations_by_exploration.get(item.id, []),
            },
            "ai_analysis": analysis,
            "knowledge_graph": {
                "status": graph_status,
                "node_count": len(nodes_by_exploration.get(item.id, [])),
                "edge_count": len(edges_by_exploration.get(item.id, [])),
                "verified_nodes": [row for row in nodes_out if row["exploration_id"] == str(item.id) and row["provenance"] == "verified"],
                "suggested_nodes": [row for row in nodes_out if row["exploration_id"] == str(item.id) and row["provenance"] == "suggested"],
                "verified_relationships": [row for row in edges_out if row["exploration_id"] == str(item.id) and row["provenance"] == "verified"],
                "suggested_relationships": [row for row in edges_out if row["exploration_id"] == str(item.id) and row["provenance"] == "suggested"],
            },
            "workflows": {
                "observed": [row for row in item_workflows if row["provenance"] == "observed"],
                "suggested": [row for row in item_workflows if row["provenance"] == "suggested"],
                "user_authored": [row for row in item_workflows if row["provenance"] == "user_authored"],
                "suggested_workflows_are_verified_journeys": False,
            },
            "test_cases": {
                "grouped_by_status_and_provenance": dict(case_groups),
                "execution_status_counts_latest_run_per_case": dict(sorted(Counter(row["latest_execution_status"] for row in item_cases).items())),
                "never_executed_definition": "No persisted TestRun references this stable test_case_id within the selected run date filter.",
                "items": item_cases,
            },
            "test_runs": {
                "status_counts": run_status_counts,
                "incomplete_count": sum(1 for row in run_rows if row["status"] in {"pending", "running"}),
                "failed_count": run_status_counts.get("failed", 0),
                "cancelled_count": run_status_counts.get("cancelled", 0),
                "unlinked_legacy_run_count": sum(1 for row in run_rows if row["test_case_id"] is None),
                "items": run_rows,
            },
            "events": {
                "count": len(events_by_exploration.get(item.id, [])),
                "items": events_by_exploration.get(item.id, []),
            },
        })

    project_value = None
    if project is not None:
        project_value = {
            "id": str(project.id), "name": _safe_text(project.name, 200),
            "target_url": _safe_url(project.target_url),
            "description": _safe_text(project_metadata.description, 1000) if project_metadata else None,
            "environment": _safe_text(project_metadata.environment, 100) if project_metadata else None,
            "archived": bool(project_metadata and project_metadata.archived_at),
            "archived_at": _utc(project_metadata.archived_at) if project_metadata else None,
            "metadata_status": "available" if project_metadata else "partial",
        }
        if project_metadata is None:
            limitations.append("Project metadata is unavailable; missing values are not treated as empty strings.")

    scope_label = _safe_text(project.name, 200) if project is not None else _safe_url(explorations[0].start_url)
    sources = {
        "projects": [str(project.id)] if project is not None else [],
        "explorations": [str(row.id) for row in explorations],
        "pages": [str(row.id) for row in pages],
        "elements": [row["id"] for row in element_rows],
        "events": [str(row.id) for row in events],
        "analyses": [str(row.id) for row in analyses],
        "interaction_observations": [str(row.id) for row in observations],
        "knowledge_graph_nodes": [str(row.id) for row in graph_nodes],
        "knowledge_graph_edges": [str(row.id) for row in graph_edges],
        "workflows": [str(row.id) for row in workflows],
        "workflow_steps": [str(row.id) for row in workflow_steps],
        "test_cases": [str(row.id) for row in test_cases],
        "test_runs": [str(row.id) for row in runs],
        "test_run_steps": [str(row.id) for row in run_steps],
        "project_exploration_associations": [
            {"project_id": str(row.project_id), "exploration_id": str(row.exploration_id)}
            for row in associations
        ],
    }
    metrics = {
        "exploration_count": len(explorations),
        "exploration_status_counts": {
            state: sum(1 for row in explorations if row.status == state)
            for state in ("queued", "running", "completed", "failed", "cancelled")
        },
        "persisted_page_rows": len(pages),
        "stored_pages_discovered_sum": sum(row.pages_discovered for row in explorations),
        "persisted_element_rows": len(elements),
        "stored_elements_discovered_sum": sum(row.elements_discovered for row in explorations),
        "persisted_event_rows": len(events),
        "stored_event_count_sum": sum(row.event_count for row in explorations),
        "test_case_rows": len(test_cases),
        "test_case_status_counts": {
            state: sum(
                1 for row in test_cases
                if ("deleted" if row.deleted_at is not None else "active" if row.is_current else "superseded") == state
            )
            for state in ("active", "superseded", "deleted")
        },
        "test_case_latest_execution_counts": dict(sorted(case_execution_counts.items())),
        "test_run_status_counts": {
            state: sum(1 for row in runs if row.status == state)
            for state in ("pending", "running", "passed", "failed", "cancelled")
        },
        "workflow_provenance_counts": {
            provenance: sum(1 for row in workflows if row.provenance == provenance)
            for provenance in ("observed", "suggested", "user_authored")
        },
        "knowledge_graph_node_count": len(graph_nodes),
        "knowledge_graph_edge_count": len(graph_edges),
        "analysis_status_counts": dict(sorted(analysis_status_counts.items())),
        "metric_definitions": {
            "exploration_count": "Number of persisted Exploration rows in the selected scope.",
            "exploration_status_counts": "Count of those Exploration rows by their persisted status; not inferred from discovered pages.",
            "persisted_page_rows": "Number of persisted ExplorationPage rows in the selected scope.",
            "stored_pages_discovered_sum": "Sum of the persisted Exploration.pages_discovered counters; reported separately from page-row count.",
            "persisted_element_rows": "Number of persisted ObservedElement rows belonging to the selected saved pages.",
            "stored_elements_discovered_sum": "Sum of the persisted Exploration.elements_discovered counters; reported separately from element-row count.",
            "persisted_event_rows": "Number of persisted ExplorationEvent rows in the selected scope.",
            "stored_event_count_sum": "Sum of the persisted Exploration.event_count counters; reported separately from event-row count.",
            "test_case_rows": "Number of persisted TestCase rows, including superseded and soft-deleted records.",
            "test_case_latest_execution_counts": "One classification per TestCase based on its latest linked TestRun by created_at then ID within the run date filter; never_executed means no linked run in that filter.",
            "test_run_status_counts": "Number of persisted TestRun rows by status within the run date filter.",
            "workflow_provenance_counts": "Number of persisted Workflow rows by provenance; suggested rows are not verified user journeys.",
            "knowledge_graph_node_count": "Number of persisted KnowledgeGraphNode rows in the selected scope.",
            "knowledge_graph_edge_count": "Number of persisted KnowledgeGraphEdge rows in the selected scope.",
            "analysis_status_counts": "Count of explorations by saved analysis status; missing analysis rows are classified not_started.",
        },
    }
    return {
        "schema_version": 1,
        "report_type": report.report_type,
        "generated_at": _utc(report.generated_at),
        "scope": {
            "type": report.report_type,
            "id": str(project.id) if report.report_type == "project" and project is not None else str(explorations[0].id) if explorations else str(report.exploration_id),
            "label": scope_label,
            "project_id": str(project.id) if project is not None else None,
            "exploration_id": str(explorations[0].id) if report.report_type == "exploration" and explorations else None,
            "archived_project_included": bool(project_metadata and project_metadata.archived_at),
        },
        "filters": report.filters or {},
        "source_record_ids": sources,
        "summary": metrics,
        "sections": {
            "project_metadata": project_value,
            "exploration_metadata": exploration_rows,
            "explorations": sections_by_exploration,
            "workflows_by_provenance": {
                "observed": [row for row in workflows_out if row["provenance"] == "observed"],
                "suggested": [row for row in workflows_out if row["provenance"] == "suggested"],
                "user_authored": [row for row in workflows_out if row["provenance"] == "user_authored"],
                "suggested_are_verified_journeys": False,
            },
            "test_run_history": runs_out,
            "known_limitations": sorted(set(limitations)),
        },
    }


def generate_exploration_report(
    session: Session,
    exploration_id: UUID,
    filters: dict[str, Any],
) -> GeneratedReport:
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise ReportSourceNotFound("Exploration was not found.")
    project_id = session.scalar(
        select(ProjectExploration.project_id).where(ProjectExploration.exploration_id == exploration_id)
    )
    if project_id is not None and session.get(Project, project_id) is None:
        project_id = None
    report = GeneratedReport(
        id=uuid4(), report_type="exploration", exploration_id=exploration_id,
        project_id=project_id, scope_label=_safe_url(exploration.start_url) or "Exploration",
        status="running", filters=filters,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    try:
        report.snapshot = _make_snapshot(session, report, exploration_id=exploration_id)
        report.status = "completed"
        report.completed_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        _mark_failed(session, report.id, exc)
        raise ReportGenerationFailed(report.id) from None
    session.refresh(report)
    return report


def generate_project_report(
    session: Session,
    project_id: UUID,
    filters: dict[str, Any],
) -> GeneratedReport:
    project = session.get(Project, project_id)
    if project is None:
        raise ReportSourceNotFound("Project was not found.")
    report = GeneratedReport(
        id=uuid4(), report_type="project", project_id=project_id, exploration_id=None,
        scope_label=_safe_text(project.name, 200) or "Project", status="running", filters=filters,
    )
    session.add(report)
    session.commit()
    session.refresh(report)
    try:
        report.snapshot = _make_snapshot(session, report, project=project)
        report.status = "completed"
        report.completed_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        _mark_failed(session, report.id, exc)
        raise ReportGenerationFailed(report.id) from None
    session.refresh(report)
    return report


def _mark_failed(session: Session, report_id: UUID, exc: Exception) -> None:
    report = session.get(GeneratedReport, report_id)
    if report is None:
        return
    report.status = "failed"
    report.error_code = "report_generation_failed"
    report.completed_at = datetime.now(UTC)
    report.snapshot = None
    session.commit()
    logger.error(
        "report_generation_failed",
        extra={"report_id": str(report_id), "exception_type": type(exc).__name__},
    )


def get_report(session: Session, report_id: UUID) -> GeneratedReport:
    report = session.get(GeneratedReport, report_id)
    if report is None:
        raise ReportNotFound("Report was not found.")
    return report


def list_reports(
    session: Session,
    *,
    report_type: str | None,
    project_id: UUID | None,
    exploration_id: UUID | None,
    generated_from: datetime | None,
    generated_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[GeneratedReport], int]:
    if project_id is not None and session.get(Project, project_id) is None:
        raise ReportSourceNotFound("Project was not found.")
    if exploration_id is not None and session.get(Exploration, exploration_id) is None:
        raise ReportSourceNotFound("Exploration was not found.")
    statement = select(GeneratedReport)
    if report_type is not None:
        statement = statement.where(GeneratedReport.report_type == report_type)
    if project_id is not None and exploration_id is not None:
        assignment = session.scalar(
            select(ProjectExploration.project_id).where(ProjectExploration.exploration_id == exploration_id)
        )
        if assignment != project_id:
            raise ValueError("The exploration is not associated with the requested project.")
        statement = statement.where(
            or_(GeneratedReport.project_id == project_id, GeneratedReport.exploration_id == exploration_id)
        )
    elif project_id is not None:
        statement = statement.where(GeneratedReport.project_id == project_id)
    elif exploration_id is not None:
        statement = statement.where(GeneratedReport.exploration_id == exploration_id)
    if generated_from is not None:
        statement = statement.where(GeneratedReport.generated_at >= generated_from)
    if generated_to is not None:
        statement = statement.where(GeneratedReport.generated_at <= generated_to)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(session.scalars(
        statement.order_by(GeneratedReport.generated_at.desc(), GeneratedReport.id.desc())
        .limit(limit).offset(offset)
    ))
    return rows, int(total)


def require_completed_report(report: GeneratedReport) -> dict[str, Any]:
    if report.status != "completed" or not isinstance(report.snapshot, dict):
        raise ReportNotReady("The saved report is not complete.")
    return {
        "id": str(report.id), "report_type": report.report_type,
        "project_id": str(report.project_id) if report.project_id else None,
        "exploration_id": str(report.exploration_id) if report.exploration_id else None,
        "scope_label": report.scope_label, "status": report.status,
        "generated_at": _utc(report.generated_at), "completed_at": _utc(report.completed_at),
        "filters": report.filters or {}, "snapshot": report.snapshot,
        "error_code": report.error_code,
    }


def _html_value(value: Any) -> str:
    if isinstance(value, dict):
        if not value:
            return '<p class="muted">No persisted items.</p>'
        parts = []
        for key, item in value.items():
            label = html.escape(str(key).replace("_", " ").title())
            parts.append(f"<section><h3>{label}</h3>{_html_value(item)}</section>")
        return "".join(parts)
    if isinstance(value, list):
        if not value:
            return '<p class="muted">None recorded.</p>'
        if all(not isinstance(item, (dict, list)) for item in value):
            return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in value) + "</ul>"
        return '<div class="items">' + "".join(f'<article class="item">{_html_value(item)}</article>' for item in value) + "</div>"
    if value is None:
        return '<span class="muted">Unavailable</span>'
    if isinstance(value, bool):
        text = "Yes" if value else "No"
    else:
        text = str(value)
    return f"<p>{html.escape(text)}</p>"


def render_report_html(report_data: dict[str, Any]) -> str:
    snapshot = report_data.get("snapshot") or {}
    scope = snapshot.get("scope") or {}
    title = f"{report_data.get('report_type', 'Report').title()} report - {scope.get('label') or report_data.get('scope_label') or 'Saved report'}"
    sections = snapshot.get("sections") if isinstance(snapshot.get("sections"), dict) else {}
    body = "".join(
        f'<section class="report-section"><h2>{html.escape(str(key).replace("_", " ").title())}</h2>{_html_value(value)}</section>'
        for key, value in sections.items()
    )
    body = (
        f'<section class="report-section"><h2>Summary metrics and definitions</h2>{_html_value(snapshot.get("summary"))}</section>'
        f'<section class="report-section"><h2>Generation filters</h2>{_html_value(report_data.get("filters") or snapshot.get("filters"))}</section>'
        f'<section class="report-section"><h2>Source record identifiers</h2>{_html_value(snapshot.get("source_record_ids"))}</section>'
        + body
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
@page{{size:A4;margin:18mm 16mm}}body{{font:11pt/1.5 Arial,sans-serif;color:#202127;max-width:900px;margin:32px auto;padding:0 24px}}
h1{{font-size:24pt;margin:0 0 8px}}h2{{font-size:16pt;border-bottom:1px solid #d8d9de;padding-bottom:5px;margin-top:28px}}h3{{font-size:12pt;margin:17px 0 6px}}
.meta,.muted{{color:#666873;font-size:9pt}}.report-section{{break-inside:auto}}.items{{display:block}}.item{{border:1px solid #e4e5e9;border-radius:4px;padding:9px 12px;margin:8px 0;break-inside:avoid}}
li{{margin:3px 0;overflow-wrap:anywhere}}p{{margin:4px 0;overflow-wrap:anywhere}}@media print{{body{{max-width:none;margin:0;padding:0}}.item{{border-radius:0}}}}
</style></head><body><header><p class="meta">SOFTWARE INTELLIGENCE | {html.escape(str(report_data.get('report_type', '')).upper())} REPORT</p>
<h1>{html.escape(title)}</h1><p class="meta">Generated {_safe_text(report_data.get('generated_at'), 64) or 'Unavailable'} | Report ID {html.escape(str(report_data.get('id', '')))}</p>
<p class="meta">Source snapshot is immutable; suggestions and verified records retain separate provenance.</p></header>{body}</body></html>"""


async def render_report_pdf(report_data: dict[str, Any]) -> bytes:
    content = render_report_html(report_data)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            async def block_requests(route: Any) -> None:
                await route.abort()
            await page.route("**/*", block_requests)
            await page.set_content(content, wait_until="load")
            return await page.pdf(
                format="A4", print_background=True, display_header_footer=True,
                margin={"top": "20mm", "bottom": "20mm", "left": "16mm", "right": "16mm"},
                header_template='<div style="font:8px Arial;color:#666;padding-left:16mm">SID</div>',
                footer_template='<div style="font:8px Arial;color:#666;width:100%;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
            )
        finally:
            await browser.close()
