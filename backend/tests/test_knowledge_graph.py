import copy
import hashlib
from uuid import uuid4

from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.models.test_case import TestCase


def _probe_key(element: ObservedElement) -> str:
    source = "\0".join(
        (
            "tab",
            str(element.ordinal),
            str(element.tag_name),
            str(element.role or ""),
            str(element.accessible_name or ""),
            str(element.selector or ""),
        )
    )
    return hashlib.sha256(source.encode()).hexdigest()


def _seed_graph_evidence(client, *, add_second_exploration: bool = False):
    exploration_id = uuid4()
    page_id, target_page_id = uuid4(), uuid4()
    form_id, field_id, tab_id, link_id = uuid4(), uuid4(), uuid4(), uuid4()
    analysis_id, case_id, observation_id = uuid4(), uuid4(), uuid4()
    start_url, target_url = "https://graph-fixture.example/", "https://graph-fixture.example/help"

    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url=start_url,
            status="completed",
            max_pages=10,
            max_depth=2,
        )
        page = ExplorationPage(
            id=page_id,
            exploration=exploration,
            url=start_url,
            title="Fixture home",
            depth=0,
            http_status=200,
            element_count=4,
        )
        target_page = ExplorationPage(
            id=target_page_id,
            exploration=exploration,
            url=target_url,
            title="Fixture help",
            depth=1,
            http_status=200,
            element_count=0,
        )
        form = ObservedElement(
            id=form_id,
            page=page,
            ordinal=0,
            kind="form",
            tag_name="form",
            role=None,
            accessible_name="Contact",
            selector="form#contact",
            attributes={"id": "contact"},
        )
        field = ObservedElement(
            id=field_id,
            page=page,
            ordinal=1,
            kind="control",
            tag_name="input",
            role="textbox",
            accessible_name="Email",
            selector="input#email",
            attributes={"inside_form": True},
        )
        tab = ObservedElement(
            id=tab_id,
            page=page,
            ordinal=2,
            kind="control",
            tag_name="button",
            role="tab",
            accessible_name="Details",
            selector="button#details",
            attributes={
                "type": "button",
                "inside_form": False,
                "inside_tablist": True,
                "aria-selected": "false",
            },
        )
        link = ObservedElement(
            id=link_id,
            page=page,
            ordinal=3,
            kind="link",
            tag_name="a",
            role="link",
            accessible_name="Help",
            selector="a.help",
            attributes={"href": target_url, "inside_form": False},
        )
        session.add_all([exploration, target_page, form, field, tab, link])
        session.flush()

        session.add(
            ExplorationAnalysis(
                id=analysis_id,
                exploration_id=exploration_id,
                status="completed",
                result={
                    "potential_workflows": [
                        {
                            "title": "Review details",
                            "objective": "Select the Details tab.",
                            "relevant_page_ids": [str(page_id)],
                            "steps": [
                                {
                                    "instruction": "Select Details.",
                                    "evidence": [{"page_id": str(page_id), "element_ids": [str(tab_id)]}],
                                }
                            ],
                            "expected_outcome": "The Details tab is selected.",
                        }
                    ]
                },
            )
        )
        session.add(
            TestCase(
                id=case_id,
                exploration_id=exploration_id,
                source="user_created",
                identity_hash="c" * 64,
                source_analysis_id=None,
                analysis_version_hash=None,
                scenario_version_hash="s" * 64,
                name="Details tab case",
                description="A persisted case.",
                definition={"schema_version": 1, "execution_kind": "workflow", "scenario": {}, "steps": []},
                is_current=True,
            )
        )
        session.add(
            InteractionObservation(
                id=observation_id,
                exploration_id=exploration_id,
                page_id=page_id,
                element_ordinal=2,
                probe_key=_probe_key(tab),
                action_type="tab",
                outcome="observed",
                reason=None,
                before_evidence={
                    "url": start_url,
                    "title": "Fixture home",
                    "target": {"tag_name": "button", "role": "tab", "name": "Details", "selected": False, "expanded": None},
                },
                after_evidence={
                    "url": start_url,
                    "title": "Fixture home",
                    "target": {"tag_name": "button", "role": "tab", "name": "Details", "selected": True, "expanded": None},
                },
                duration_ms=30,
            )
        )

        second_exploration_id = None
        if add_second_exploration:
            second_exploration_id = uuid4()
            session.add(
                Exploration(
                    id=second_exploration_id,
                    start_url="https://other-fixture.example/",
                    status="completed",
                    max_pages=1,
                    max_depth=0,
                )
            )
        session.commit()
    return exploration_id, page_id, target_page_id, field_id, analysis_id, case_id, observation_id, second_exploration_id


def test_graph_build_is_evidence_backed_and_repeatable(client):
    exploration_id, page_id, target_page_id, field_id, analysis_id, case_id, observation_id, _ = _seed_graph_evidence(client)
    path = f"/api/v1/explorations/{exploration_id}/knowledge-graph"

    built = client.post(path + "/rebuild")
    assert built.status_code == 200, built.text
    summary = built.json()
    assert summary["nodes_written"] > 0
    assert summary["edges_written"] > 0
    assert summary["suggested_nodes"] == 1
    assert summary["skipped_invalid_workflows"] == 0
    assert summary["skipped_invalid_interactions"] == 0

    response = client.get(path)
    assert response.status_code == 200, response.text
    graph = response.json()
    nodes = {item["id"]: item for item in graph["nodes"]}
    edges = graph["edges"]
    assert graph["total_nodes"] == summary["nodes_written"]
    assert graph["total_edges"] == summary["edges_written"]
    assert all(item["exploration_id"] == str(exploration_id) and item["evidence"] for item in nodes.values())
    assert all(
        item["exploration_id"] == str(exploration_id)
        and item["evidence"]
        and item["source_node_id"] in nodes
        and item["target_node_id"] in nodes
        for item in edges
    )

    edge_types = {item["edge_type"] for item in edges}
    assert {"contains_page", "contains_element", "links_to", "has_test_case", "transitions_to_state", "participates_in_workflow"} <= edge_types
    assert "belongs_to_form" not in edge_types  # inside_form alone does not identify a parent form.
    assert all(item["provenance"] == "suggested" for item in edges if item["edge_type"] == "participates_in_workflow")
    workflow = next(item for item in nodes.values() if item["node_type"] == "workflow")
    assert workflow["provenance"] == "suggested"
    assert any(ref["source_type"] == "analysis" and ref["source_id"] == str(analysis_id) for ref in workflow["evidence"])
    state_nodes = [item for item in nodes.values() if item["node_type"] == "interaction_state"]
    assert len(state_nodes) == 2
    assert all(any(ref["source_id"] == str(observation_id) for ref in item["evidence"]) for item in state_nodes)
    assert any(item["node_type"] == "test_case" and item["source_key"] == str(case_id) for item in nodes.values())
    assert any(item["node_type"] == "ui_element" and item["source_key"] == str(field_id) for item in nodes.values())
    assert any(item["node_type"] == "page" and item["source_key"] == str(target_page_id) for item in nodes.values())

    original_node_ids = {item["id"] for item in graph["nodes"]}
    original_edge_ids = {item["id"] for item in edges}
    second_build = client.post(path + "/rebuild")
    assert second_build.status_code == 200
    second_graph = client.get(path).json()
    assert {item["id"] for item in second_graph["nodes"]} == original_node_ids
    assert {item["id"] for item in second_graph["edges"]} == original_edge_ids
    assert second_graph["total_nodes"] == graph["total_nodes"]
    assert second_graph["total_edges"] == graph["total_edges"]


def test_graph_filters_are_scoped_and_invalid_page_evidence_is_not_linked(client):
    exploration_id, page_id, _target_page_id, _field_id, _analysis_id, _case_id, _observation_id, other_exploration_id = (
        _seed_graph_evidence(client, add_second_exploration=True)
    )
    path = f"/api/v1/explorations/{exploration_id}/knowledge-graph"
    other_path = f"/api/v1/explorations/{other_exploration_id}/knowledge-graph"
    assert client.post(path + "/rebuild").status_code == 200
    assert client.post(other_path + "/rebuild").status_code == 200

    page_graph = client.get(path, params={"page_id": str(page_id)}).json()
    assert any(node["node_type"] == "page" and node["source_key"] == str(page_id) for node in page_graph["nodes"])
    assert all(node["exploration_id"] == str(exploration_id) for node in page_graph["nodes"])
    assert client.get(path, params={"page_id": str(uuid4())}).status_code == 404

    element_graph = client.get(path, params={"node_type": "ui_element"}).json()
    assert any(node["node_type"] == "ui_element" for node in element_graph["nodes"])
    page_elements = client.get(
        path,
        params={"node_type": "ui_element", "page_id": str(page_id)},
    ).json()
    matching_elements = [node for node in page_elements["nodes"] if node["node_type"] == "ui_element"]
    assert matching_elements
    assert all(node["page_id"] == str(page_id) for node in matching_elements)
    assert client.get(other_path).json()["total_nodes"] < client.get(path).json()["total_nodes"]

    suggested = client.get(path, params={"provenance": "suggested"}).json()
    assert any(node["provenance"] == "suggested" and node["node_type"] == "workflow" for node in suggested["nodes"])
    assert any(node["provenance"] == "verified" for node in suggested["nodes"])  # Evidence endpoints remain inspectable.
    assert all(edge["provenance"] == "suggested" for edge in suggested["edges"])

    workflow_page = client.get(
        path,
        params={"provenance": "suggested", "page_id": str(page_id)},
    ).json()
    assert any(node["node_type"] == "workflow" for node in workflow_page["nodes"])
    assert all(node["exploration_id"] == str(exploration_id) for node in workflow_page["nodes"])


def test_graph_skips_stale_analysis_and_interaction_evidence(client):
    exploration_id, page_id, _target_page_id, _field_id, analysis_id, _case_id, observation_id, _ = _seed_graph_evidence(client)
    with client.app.state.test_session_factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        result = copy.deepcopy(analysis.result)
        result["potential_workflows"][0]["steps"][0]["evidence"][0]["element_ids"] = [str(uuid4())]
        analysis.result = result
        observation = session.get(InteractionObservation, observation_id)
        observation.outcome = "failed"
        observation.reason = "stale_fixture"
        session.commit()

    path = f"/api/v1/explorations/{exploration_id}/knowledge-graph"
    built = client.post(path + "/rebuild")
    assert built.status_code == 200
    summary = built.json()
    assert summary["skipped_invalid_workflows"] == 1
    assert summary["skipped_invalid_interactions"] == 0  # Only observed rows are considered for transitions.
    graph = client.get(path).json()
    assert not any(node["node_type"] == "workflow" for node in graph["nodes"])
    assert not any(node["node_type"] == "interaction_state" for node in graph["nodes"])
    assert not any(edge["edge_type"] == "transitions_to_state" for edge in graph["edges"])
    assert client.get(path, params={"node_type": "ui_element", "page_id": str(page_id)}).status_code == 200


def test_form_membership_and_links_require_exact_same_exploration_evidence(client):
    exploration_id, page_id, target_page_id, field_id, _analysis_id, _case_id, _observation_id, _ = _seed_graph_evidence(client)
    with client.app.state.test_session_factory() as session:
        field = session.get(ObservedElement, field_id)
        form = next(element for element in session.get(ExplorationPage, page_id).elements if element.kind == "form")
        field.attributes = {**field.attributes, "form_element_id": str(form.id)}
        cross_page_field = ObservedElement(
            page_id=target_page_id,
            ordinal=0,
            kind="control",
            tag_name="input",
            role="textbox",
            accessible_name="Remote field",
            selector="input.remote",
            attributes={"inside_form": True, "form_element_id": str(form.id)},
        )
        link = next(element for element in session.get(ExplorationPage, page_id).elements if element.kind == "link")
        link.attributes = {**link.attributes, "href": "https://outside.example/"}
        session.add(cross_page_field)
        session.commit()

    path = f"/api/v1/explorations/{exploration_id}/knowledge-graph"
    assert client.post(path + "/rebuild").status_code == 200
    graph = client.get(path).json()
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = graph["edges"]
    membership = [edge for edge in edges if edge["edge_type"] == "belongs_to_form"]
    assert len(membership) == 1
    assert nodes[membership[0]["source_node_id"]]["source_key"] == str(field_id)
    assert nodes[membership[0]["target_node_id"]]["node_type"] == "form"
    assert not any(edge["edge_type"] == "links_to" for edge in edges)


def test_graph_read_reports_unbuilt_state_and_respects_node_limit(client):
    exploration_id, *_ = _seed_graph_evidence(client)
    path = f"/api/v1/explorations/{exploration_id}/knowledge-graph"
    empty = client.get(path)
    assert empty.status_code == 200
    assert empty.json()["nodes"] == []
    assert empty.json()["edges"] == []

    assert client.post(path + "/rebuild").status_code == 200
    limited = client.get(path, params={"limit": 1})
    assert limited.status_code == 200
    assert limited.json()["returned_nodes"] == 1
    assert limited.json()["truncated"] is True
