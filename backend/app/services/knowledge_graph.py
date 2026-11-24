"""Build a deterministic knowledge graph only from saved evidence."""

import hashlib
import json
from typing import Any
from urllib.parse import urljoin
from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload
from pydantic import ValidationError

from app.core.url_security import UnsafeTargetUrl, normalized_http_url, same_origin
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.test_case import TestCase
from app.schemas.analysis import WorkflowSuggestion


_GRAPH_NAMESPACE = UUID("16e198b4-d888-5e3f-96b9-235dbd8cc330")


class GraphSourceNotFound(LookupError):
    """The requested exploration is missing."""


def _canonical_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()


def _node_id(exploration_id: UUID, node_type: str, source_key: str) -> UUID:
    return uuid5(_GRAPH_NAMESPACE, f"node:{exploration_id}:{node_type}:{source_key}")


def _edge_id(
    exploration_id: UUID,
    source_node_id: UUID,
    target_node_id: UUID,
    edge_type: str,
    provenance: str,
) -> UUID:
    return uuid5(
        _GRAPH_NAMESPACE,
        f"edge:{exploration_id}:{source_node_id}:{target_node_id}:{edge_type}:{provenance}",
    )


def _evidence(
    source_type: str,
    source_id: UUID | str,
    *,
    page_id: UUID | None = None,
    element_id: UUID | None = None,
) -> dict[str, str | None]:
    return {
        "source_type": source_type,
        "source_id": str(source_id),
        "page_id": str(page_id) if page_id else None,
        "element_id": str(element_id) if element_id else None,
    }


def _clean_label(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    label = " ".join(value.split())[:300]
    return label or fallback


def _probe_key_matches(observation: InteractionObservation, element: ObservedElement) -> bool:
    source = "\0".join(
        (
            observation.action_type,
            str(element.ordinal),
            str(element.tag_name),
            str(element.role or ""),
            str(element.accessible_name or ""),
            str(element.selector or ""),
        )
    )
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest() == observation.probe_key


def _graph_interaction_evidence(
    observation: InteractionObservation,
    exploration: Exploration,
    pages_by_id: dict[UUID, ExplorationPage],
) -> tuple[ExplorationPage, ObservedElement, str, bool, bool, str] | None:
    """Validate recorded facts without applying test-execution route allowlists."""
    if observation.outcome != "observed" or observation.reason is not None:
        return None
    if observation.action_type not in {"tab", "disclosure"}:
        return None
    page = pages_by_id.get(observation.page_id)
    if page is None or page.exploration_id != exploration.id:
        return None
    try:
        page_url = normalized_http_url(page.url)
        before_url = normalized_http_url(str((observation.before_evidence or {}).get("url", "")))
        after_url = normalized_http_url(str((observation.after_evidence or {}).get("url", "")))
        if (
            before_url != page_url
            or after_url != page_url
            or not same_origin(exploration.start_url, page_url)
        ):
            return None
    except (UnsafeTargetUrl, TypeError, ValueError):
        return None

    element = next((item for item in page.elements if item.ordinal == observation.element_ordinal), None)
    if element is None or not _probe_key_matches(observation, element):
        return None
    attributes = element.attributes or {}
    if (
        attributes.get("inside_form") is not False
        or attributes.get("disabled")
        or str(attributes.get("aria-disabled", "")).casefold() == "true"
        or element.tag_name.casefold() not in {"button", "div"}
        or (element.tag_name.casefold() == "button" and str(attributes.get("type", "")).casefold() != "button")
    ):
        return None

    if observation.action_type == "tab":
        attribute, expected_role, state_key = "aria-selected", "tab", "selected"
        if element.role != "tab" or attributes.get("inside_tablist") is not True:
            return None
    else:
        attribute, expected_role, state_key = "aria-expanded", "button", "expanded"
        if (
            element.role != "button"
            or attributes.get("controls_valid") is not True
            or not isinstance(attributes.get("aria-controls"), str)
        ):
            return None

    before = observation.before_evidence.get("target") if isinstance(observation.before_evidence, dict) else None
    after = observation.after_evidence.get("target") if isinstance(observation.after_evidence, dict) else None
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    name = element.accessible_name or ""
    if not name or before.get("name") != name or after.get("name") != name:
        return None
    if any(
        str(target.get("tag_name", "")).casefold() != element.tag_name.casefold()
        or str(target.get("role", "")).casefold() != expected_role
        for target in (before, after)
    ):
        return None
    before_state, after_state = before.get(state_key), after.get(state_key)
    if not isinstance(before_state, bool) or not isinstance(after_state, bool) or before_state == after_state:
        return None
    saved_before = attributes.get(attribute)
    if str(saved_before).casefold() != str(before_state).casefold():
        return None
    for evidence in (observation.before_evidence, observation.after_evidence):
        title = evidence.get("title") if isinstance(evidence, dict) else None
        if title is not None and title != (page.title or "")[:160]:
            return None
    return page, element, attribute, before_state, after_state, name


def _upsert_graph_rows(
    session: Session,
    exploration_id: UUID,
    nodes: dict[UUID, dict[str, Any]],
    edges: dict[UUID, dict[str, Any]],
) -> None:
    existing_nodes = {
        row.id: row
        for row in session.scalars(
            select(KnowledgeGraphNode).where(KnowledgeGraphNode.exploration_id == exploration_id)
        )
    }
    existing_edges = {
        row.id: row
        for row in session.scalars(
            select(KnowledgeGraphEdge).where(KnowledgeGraphEdge.exploration_id == exploration_id)
        )
    }

    for node_id, values in nodes.items():
        row = existing_nodes.pop(node_id, None)
        if row is None:
            session.add(KnowledgeGraphNode(id=node_id, exploration_id=exploration_id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    for edge_id, values in edges.items():
        row = existing_edges.pop(edge_id, None)
        if row is None:
            session.add(KnowledgeGraphEdge(id=edge_id, exploration_id=exploration_id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)

    # Prune only stale generated graph rows for this exploration. Source
    # explorations, analyses, cases, runs, and observations are never touched.
    for edge_id in existing_edges:
        session.execute(delete(KnowledgeGraphEdge).where(KnowledgeGraphEdge.id == edge_id))
    for node_id in existing_nodes:
        session.execute(delete(KnowledgeGraphNode).where(KnowledgeGraphNode.id == node_id))
    session.flush()


def synchronize_knowledge_graph(session: Session, exploration_id: UUID) -> dict[str, int | UUID]:
    """Build and idempotently synchronize one exploration's verified/suggested graph."""
    exploration = session.scalar(
        select(Exploration).where(Exploration.id == exploration_id).with_for_update()
    )
    if exploration is None:
        raise GraphSourceNotFound("Exploration was not found.")

    pages = list(
        session.scalars(
            select(ExplorationPage)
            .options(selectinload(ExplorationPage.elements))
            .where(ExplorationPage.exploration_id == exploration_id)
            .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.id)
        )
    )
    pages_by_id = {page.id: page for page in pages}
    pages_by_url: dict[str, ExplorationPage] = {}
    for page in pages:
        try:
            pages_by_url[normalized_http_url(page.url)] = page
        except (UnsafeTargetUrl, TypeError, ValueError):
            continue

    analysis = session.scalar(
        select(ExplorationAnalysis).where(
            ExplorationAnalysis.exploration_id == exploration_id,
            ExplorationAnalysis.status == "completed",
        )
    )
    test_cases = list(
        session.scalars(
            select(TestCase).where(TestCase.exploration_id == exploration_id).order_by(TestCase.id)
        )
    )
    observations = list(
        session.scalars(
            select(InteractionObservation)
            .where(InteractionObservation.exploration_id == exploration_id)
            .order_by(InteractionObservation.id)
        )
    )

    nodes: dict[UUID, dict[str, Any]] = {}
    edges: dict[UUID, dict[str, Any]] = {}
    element_nodes: dict[UUID, UUID] = {}
    element_page_ids: dict[UUID, UUID] = {}
    page_nodes: dict[UUID, UUID] = {}
    form_nodes: dict[UUID, UUID] = {}
    skipped_workflows = 0
    skipped_interactions = 0

    def add_node(
        node_type: str,
        source_key: str,
        label: str,
        evidence: list[dict[str, Any]],
        *,
        page_id: UUID | None = None,
        provenance: str = "verified",
        properties: dict[str, Any] | None = None,
    ) -> UUID:
        if not evidence:
            raise ValueError("Graph nodes must have source evidence.")
        node_id = _node_id(exploration_id, node_type, source_key)
        values = nodes.get(node_id)
        if values is None:
            nodes[node_id] = {
                "node_type": node_type,
                "source_key": source_key,
                "label": _clean_label(label, node_type),
                "page_id": page_id,
                "provenance": provenance,
                "evidence": evidence,
                "properties": properties or {},
            }
        else:
            seen = {json.dumps(item, sort_keys=True) for item in values["evidence"]}
            values["evidence"].extend(item for item in evidence if json.dumps(item, sort_keys=True) not in seen)
        return node_id

    def add_edge(
        source_node_id: UUID,
        target_node_id: UUID,
        edge_type: str,
        evidence: list[dict[str, Any]],
        *,
        provenance: str = "verified",
        properties: dict[str, Any] | None = None,
    ) -> UUID:
        if source_node_id not in nodes or target_node_id not in nodes or not evidence:
            raise ValueError("Graph edges require existing endpoints and source evidence.")
        edge_id = _edge_id(exploration_id, source_node_id, target_node_id, edge_type, provenance)
        values = edges.get(edge_id)
        if values is None:
            edges[edge_id] = {
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
                "edge_type": edge_type,
                "provenance": provenance,
                "evidence": evidence,
                "properties": properties or {},
            }
        else:
            seen = {json.dumps(item, sort_keys=True) for item in values["evidence"]}
            values["evidence"].extend(item for item in evidence if json.dumps(item, sort_keys=True) not in seen)
        return edge_id

    exploration_node = add_node(
        "exploration",
        str(exploration.id),
        f"Exploration · {exploration.status}",
        [_evidence("exploration", exploration.id)],
        properties={"status": exploration.status, "start_url": exploration.start_url},
    )

    for page in pages:
        page_node = add_node(
            "page",
            str(page.id),
            page.title or page.url,
            [_evidence("page", page.id, page_id=page.id)],
            page_id=page.id,
            properties={
                "url": page.url,
                "title": page.title,
                "http_status": page.http_status,
                "depth": page.depth,
                "element_count": page.element_count,
            },
        )
        page_nodes[page.id] = page_node
        add_edge(
            exploration_node,
            page_node,
            "contains_page",
            [_evidence("page", page.id, page_id=page.id)],
        )

    for page in pages:
        page_node = page_nodes[page.id]
        for element in page.elements:
            element_label = element.accessible_name or element.label or f"<{element.tag_name}>"
            element_node = add_node(
                "ui_element",
                str(element.id),
                element_label,
                [_evidence("element", element.id, page_id=page.id, element_id=element.id)],
                page_id=page.id,
                properties={
                    "tag_name": element.tag_name,
                    "role": element.role,
                    "kind": element.kind,
                    "ordinal": element.ordinal,
                    "accessible_name": _clean_label(element.accessible_name, "") or None,
                },
            )
            element_nodes[element.id] = element_node
            element_page_ids[element.id] = page.id
            add_edge(
                page_node,
                element_node,
                "contains_element",
                [
                    _evidence("page", page.id, page_id=page.id),
                    _evidence("element", element.id, page_id=page.id, element_id=element.id),
                ],
            )

            attributes = element.attributes or {}
            if element.kind == "form" or element.tag_name.casefold() == "form":
                form_label = attributes.get("aria-label") or element.label or f"Form {element.ordinal + 1}"
                form_node = add_node(
                    "form",
                    str(element.id),
                    str(form_label),
                    [_evidence("element", element.id, page_id=page.id, element_id=element.id)],
                    page_id=page.id,
                    properties={"tag_name": element.tag_name, "ordinal": element.ordinal},
                )
                form_nodes[element.id] = form_node
                add_edge(
                    page_node,
                    form_node,
                    "contains_element",
                    [_evidence("element", element.id, page_id=page.id, element_id=element.id)],
                )

            href = attributes.get("href")
            if (element.kind == "link" or element.tag_name.casefold() == "a") and isinstance(href, str):
                try:
                    target_url = normalized_http_url(urljoin(page.url, href))
                    target_page = pages_by_url.get(target_url)
                    if (
                        target_page
                        and same_origin(exploration.start_url, page.url)
                        and same_origin(exploration.start_url, target_url)
                    ):
                        add_edge(
                            page_node,
                            page_nodes[target_page.id],
                            "links_to",
                            [
                                _evidence("page", page.id, page_id=page.id),
                                _evidence("element", element.id, page_id=page.id, element_id=element.id),
                                _evidence("page", target_page.id, page_id=target_page.id),
                            ],
                        )
                except (UnsafeTargetUrl, TypeError, ValueError):
                    pass

    # A boolean inside_form flag does not identify a parent form. Create this
    # edge only when a future persisted element explicitly names the form node
    # by stable element UUID; current crawler rows do not record that relation.
    for page in pages:
        for element in page.elements:
            attributes = element.attributes or {}
            referenced_form = attributes.get("form_element_id")
            if not attributes.get("inside_form") or not isinstance(referenced_form, str):
                continue
            try:
                form_id = UUID(referenced_form)
            except ValueError:
                continue
            if (
                form_id not in form_nodes
                or form_id not in element_nodes
                or element_page_ids.get(form_id) != page.id
            ):
                continue
            add_edge(
                element_nodes[element.id],
                form_nodes[form_id],
                "belongs_to_form",
                [
                    _evidence("element", element.id, page_id=page.id, element_id=element.id),
                    _evidence("element", form_id, page_id=page.id, element_id=form_id),
                ],
            )

    if analysis is not None and isinstance(analysis.result, dict):
        raw_workflows = analysis.result.get("potential_workflows", [])
        if isinstance(raw_workflows, list):
            for workflow_index, raw_workflow in enumerate(raw_workflows):
                try:
                    workflow = WorkflowSuggestion.model_validate(raw_workflow)
                    if not set(workflow.relevant_page_ids).issubset(pages_by_id):
                        raise ValueError("workflow references an unsaved page")
                    references: list[dict[str, Any]] = [_evidence("analysis", analysis.id)]
                    participations: set[tuple[str, UUID, UUID | None]] = set()
                    for step in workflow.steps:
                        for item in step.evidence:
                            evidence_page = pages_by_id.get(item.page_id)
                            if (
                                evidence_page is None
                                or item.page_id not in workflow.relevant_page_ids
                                or not same_origin(exploration.start_url, evidence_page.url)
                            ):
                                raise ValueError("workflow evidence references an unsaved page")
                            references.append(_evidence("page", item.page_id, page_id=item.page_id))
                            if item.element_ids:
                                for element_id in item.element_ids:
                                    element_node = element_nodes.get(element_id)
                                    element = next((row for row in evidence_page.elements if row.id == element_id), None)
                                    if element_node is None or element is None:
                                        raise ValueError("workflow evidence references an unsaved element")
                                    references.append(
                                        _evidence("element", element_id, page_id=item.page_id, element_id=element_id)
                                    )
                                    participations.add(("element", element_node, item.page_id))
                            else:
                                participations.add(("page", page_nodes[item.page_id], item.page_id))
                    if len(references) == 1:
                        raise ValueError("workflow has no persisted evidence")
                    workflow_data = workflow.model_dump(mode="json")
                    workflow_hash = _canonical_hash(workflow_data)
                    workflow_node = add_node(
                        "workflow",
                        f"{analysis.id}:{workflow_hash}",
                        workflow.title,
                        references,
                        provenance="suggested",
                        properties={
                            "objective": workflow.objective,
                            "expected_outcome": workflow.expected_outcome,
                            "step_count": len(workflow.steps),
                            "analysis_id": str(analysis.id),
                            "suggestion_index": workflow_index,
                        },
                    )
                    for _kind, participant_node, participant_page_id in participations:
                        participation_evidence = [
                            _evidence("analysis", analysis.id),
                            _evidence("page", participant_page_id, page_id=participant_page_id),
                        ]
                        if _kind == "element":
                            source_element = next(
                                element
                                for page in pages
                                for element in page.elements
                                if element_nodes.get(element.id) == participant_node
                            )
                            participation_evidence.append(
                                _evidence(
                                    "element",
                                    source_element.id,
                                    page_id=participant_page_id,
                                    element_id=source_element.id,
                                )
                            )
                        add_edge(
                            participant_node,
                            workflow_node,
                            "participates_in_workflow",
                            participation_evidence,
                            provenance="suggested",
                        )
                except (ValidationError, ValueError, TypeError, KeyError):
                    # An invalid AI suggestion is omitted from this graph build;
                    # persisted verified exploration facts remain unaffected.
                    skipped_workflows += 1

    for test_case in test_cases:
        test_case_node = add_node(
            "test_case",
            str(test_case.id),
            test_case.name,
            [_evidence("test_case", test_case.id)],
            properties={
                "source": test_case.source,
                "status": "deleted" if test_case.deleted_at is not None else ("active" if test_case.is_current else "superseded"),
                "execution_kind": test_case.definition.get("execution_kind") if isinstance(test_case.definition, dict) else None,
            },
        )
        add_edge(
            exploration_node,
            test_case_node,
            "has_test_case",
            [_evidence("test_case", test_case.id)],
        )

    for observation in observations:
        validated = _graph_interaction_evidence(observation, exploration, pages_by_id)
        if validated is None:
            if observation.outcome == "observed":
                skipped_interactions += 1
            continue
        page, element, attribute, before_value, after_value, name = validated
        element_node = element_nodes.get(element.id)
        if element_node is None:
            skipped_interactions += 1
            continue
        state_nodes: dict[bool, UUID] = {}
        state_key = "selected" if attribute == "aria-selected" else "expanded"
        for phase, value in (("before", before_value), ("after", after_value)):
            state_nodes[value] = add_node(
                "interaction_state",
                f"{observation.id}:{phase}:{str(value).lower()}",
                f"{attribute} {str(value).lower()} · {name}",
                [
                    _evidence("interaction_observation", observation.id, page_id=page.id, element_id=element.id),
                    _evidence("page", page.id, page_id=page.id),
                    _evidence("element", element.id, page_id=page.id, element_id=element.id),
                ],
                page_id=page.id,
                properties={
                    "phase": phase,
                    "interaction_type": observation.action_type,
                    "attribute": attribute,
                    "state_key": state_key,
                    "value": value,
                    "control_name": name,
                },
            )
        add_edge(
            state_nodes[before_value],
            state_nodes[after_value],
            "transitions_to_state",
            [
                _evidence("interaction_observation", observation.id, page_id=page.id, element_id=element.id),
                _evidence("page", page.id, page_id=page.id),
                _evidence("element", element.id, page_id=page.id, element_id=element.id),
            ],
            properties={
                "interaction_type": observation.action_type,
                "control_node_id": str(element_node),
                "attribute": attribute,
                "control_name": name,
            },
        )

    # Stable identities and unique DB constraints prevent duplicate records.
    # Sorting makes persistence and API output deterministic across rebuilds.
    _upsert_graph_rows(session, exploration_id, nodes, edges)
    return {
        "exploration_id": exploration_id,
        "nodes_written": len(nodes),
        "edges_written": len(edges),
        "verified_nodes": sum(item["provenance"] == "verified" for item in nodes.values()),
        "suggested_nodes": sum(item["provenance"] == "suggested" for item in nodes.values()),
        "verified_edges": sum(item["provenance"] == "verified" for item in edges.values()),
        "suggested_edges": sum(item["provenance"] == "suggested" for item in edges.values()),
        "skipped_invalid_workflows": skipped_workflows,
        "skipped_invalid_interactions": skipped_interactions,
    }


def read_knowledge_graph(
    session: Session,
    exploration_id: UUID,
    *,
    node_type: str | None = None,
    page_id: UUID | None = None,
    provenance: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Read a bounded induced subgraph and preserve a count when it is truncated."""
    if session.get(Exploration, exploration_id) is None:
        raise GraphSourceNotFound("Exploration was not found.")
    all_nodes = list(
        session.scalars(
            select(KnowledgeGraphNode)
            .where(KnowledgeGraphNode.exploration_id == exploration_id)
            .order_by(KnowledgeGraphNode.node_type, KnowledgeGraphNode.label, KnowledgeGraphNode.id)
        )
    )
    all_edges = list(
        session.scalars(
            select(KnowledgeGraphEdge)
            .where(KnowledgeGraphEdge.exploration_id == exploration_id)
            .order_by(KnowledgeGraphEdge.edge_type, KnowledgeGraphEdge.id)
        )
    )
    persisted_node_ids = {node.id for node in all_nodes}
    all_edges = [
        edge for edge in all_edges
        if edge.source_node_id in persisted_node_ids and edge.target_node_id in persisted_node_ids
    ]
    eligible = [node for node in all_nodes if provenance is None or node.provenance == provenance]
    seeds: set[UUID] = set()
    for node in eligible:
        matches_type = node_type is None or node.node_type == node_type
        matches_page = page_id is None or node.page_id == page_id or (
            node.node_type == "page" and node.source_key == str(page_id)
        ) or (
            node.node_type == "workflow"
            and any(isinstance(item, dict) and item.get("page_id") == str(page_id) for item in node.evidence)
        )
        if matches_type and matches_page:
            seeds.add(node.id)

    # A focused view includes direct context around matching nodes. Provenance
    # filters select the relationship layer; endpoint nodes can retain their
    # own provenance so suggested workflows stay connected to verified evidence.
    visible = set(seeds)
    if node_type is not None or page_id is not None or provenance is not None:
        for edge in all_edges:
            if provenance is not None and edge.provenance != provenance:
                continue
            if edge.source_node_id in seeds:
                visible.add(edge.target_node_id)
            if edge.target_node_id in seeds:
                visible.add(edge.source_node_id)

    node_by_id = {node.id: node for node in all_nodes}
    selected_nodes = [node_by_id[node_id] for node_id in visible if node_id in node_by_id]
    total_nodes, total_edges = len(selected_nodes), sum(
        edge.source_node_id in visible and edge.target_node_id in visible
        and (provenance is None or edge.provenance == provenance)
        for edge in all_edges
    )
    selected_nodes.sort(key=lambda item: (item.node_type, item.label.casefold(), str(item.id)))
    truncated = len(selected_nodes) > limit
    selected_nodes = selected_nodes[:limit]
    selected_ids = {node.id for node in selected_nodes}
    selected_edges = [
        edge for edge in all_edges
        if edge.source_node_id in selected_ids
        and edge.target_node_id in selected_ids
        and (provenance is None or edge.provenance == provenance)
    ]
    return {
        "exploration_id": exploration_id,
        "nodes": selected_nodes,
        "edges": selected_edges,
        "total_nodes": total_nodes,
        "total_edges": total_edges,
        "returned_nodes": len(selected_nodes),
        "truncated": truncated,
    }
