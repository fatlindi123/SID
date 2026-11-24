import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models.exploration import Exploration, ExplorationEvent, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.project import Project, ProjectExploration, ProjectMetadata
from app.models.test_case import TestCase
from app.models.workflow import Workflow, WorkflowStep


def _probe_key(element: ObservedElement) -> str:
    source = "\0".join(("tab", str(element.ordinal), element.tag_name, element.role or "", element.accessible_name or "", element.selector or ""))
    return hashlib.sha256(source.encode()).hexdigest()


def _seed(client, *, project: bool = False):
    exploration_id = uuid4()
    page_id, target_id = uuid4(), uuid4()
    link_id, tab_id = uuid4(), uuid4()
    case_id, observation_id = uuid4(), uuid4()
    start_url = "https://workflow-fixture.example/"
    target_url = "https://workflow-fixture.example/help"
    project_id = uuid4() if project else None
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(id=exploration_id, start_url=start_url, status="completed", max_pages=5, max_depth=1)
        source_page = ExplorationPage(id=page_id, exploration=exploration, url=start_url, title="Fixture home", depth=0, http_status=200, element_count=2)
        target_page = ExplorationPage(id=target_id, exploration=exploration, url=target_url, title="Fixture help", depth=1, http_status=200, element_count=0)
        link = ObservedElement(
            id=link_id, page=source_page, ordinal=0, kind="link", tag_name="a", role="link",
            accessible_name="Help", selector="a", attributes={"href": target_url, "inside_form": False},
        )
        tab = ObservedElement(
            id=tab_id, page=source_page, ordinal=1, kind="control", tag_name="button", role="tab",
            accessible_name="Details", selector="button#details",
            attributes={"type": "button", "inside_form": False, "inside_tablist": True, "aria-selected": "false"},
        )
        session.add_all([exploration, target_page, link, tab])
        session.flush()
        source_node = KnowledgeGraphNode(
            exploration_id=exploration_id, node_type="page", source_key=str(page_id), label="Fixture home",
            page_id=page_id, provenance="verified", evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}], properties={"url": start_url},
        )
        target_node = KnowledgeGraphNode(
            exploration_id=exploration_id, node_type="page", source_key=str(target_id), label="Fixture help",
            page_id=target_id, provenance="verified", evidence=[{"source_type": "page", "source_id": str(target_id), "page_id": str(target_id)}], properties={"url": target_url},
        )
        session.add_all([source_node, target_node])
        session.flush()
        session.add(KnowledgeGraphEdge(
            exploration_id=exploration_id, source_node_id=source_node.id, target_node_id=target_node.id,
            edge_type="links_to", provenance="verified", evidence=[
                {"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)},
                {"source_type": "element", "source_id": str(link_id), "page_id": str(page_id), "element_id": str(link_id)},
                {"source_type": "page", "source_id": str(target_id), "page_id": str(target_id)},
            ], properties={},
        ))
        observation = InteractionObservation(
            id=observation_id, exploration_id=exploration_id, page_id=page_id, element_ordinal=1,
            probe_key=_probe_key(tab), action_type="tab", outcome="observed", before_evidence={
                "url": start_url, "title": "Fixture home",
                "target": {"tag_name": "button", "role": "tab", "name": "Details", "selected": False, "expanded": None},
            }, after_evidence={
                "url": start_url, "title": "Fixture home",
                "target": {"tag_name": "button", "role": "tab", "name": "Details", "selected": True, "expanded": None},
            }, duration_ms=20,
        )
        session.add(observation)
        # A valid saved manual case contributes an ordered, user-authored step.
        case_definition = {
            "schema_version": 1,
            "execution_kind": "workflow",
            "scenario": {
                "title": "Visit home", "objective": "Inspect the saved home page.",
                "relevant_page_ids": [str(page_id), str(target_id)],
                "steps": [
                    {"instruction": "Navigate to home page", "evidence": [{"page_id": str(page_id), "element_ids": []}]},
                    {"instruction": "Navigate to saved help page", "evidence": [{"page_id": str(target_id), "element_ids": []}]},
                ],
                "expected_outcome": "The saved home and help pages are available.",
            },
            "steps": [
                {
                    "ordinal": 0, "instruction": "Navigate to home page",
                    "action": {"kind": "navigate", "page_id": str(page_id), "url": start_url, "title": "Fixture home"},
                    "expected_evidence": {"page_id": str(page_id), "url": start_url, "title": "Fixture home"},
                },
                {
                    "ordinal": 1, "instruction": "Navigate to saved help page",
                    "action": {"kind": "navigate", "page_id": str(target_id), "url": target_url, "title": "Fixture help"},
                    "expected_evidence": {"page_id": str(target_id), "url": target_url, "title": "Fixture help"},
                },
            ],
        }
        case_hash = hashlib.sha256(json.dumps(case_definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        session.add(TestCase(
            id=case_id, exploration_id=exploration_id, source="user_created", identity_hash="i" * 64,
            source_analysis_id=None, analysis_version_hash=None, scenario_version_hash=case_hash,
            name="Visit home", description="Inspect the saved home page.", is_current=True,
            definition=case_definition,
        ))
        if project_id:
            session.add(Project(id=project_id, name="Fixture project", target_url=start_url))
            session.add(ProjectMetadata(project_id=project_id, description=""))
            session.add(ProjectExploration(project_id=project_id, exploration_id=exploration_id))
        session.commit()
    return exploration_id, page_id, target_id, link_id, observation_id, case_id, project_id


def _path(exploration_id):
    return f"/api/v1/explorations/{exploration_id}/workflows"


def test_rebuild_creates_deterministic_evidence_backed_workflows_and_preserves_edits(client):
    exploration_id, page_id, target_id, link_id, observation_id, case_id, _ = _seed(client)
    path = _path(exploration_id)

    first = client.post(path + "/rebuild")
    assert first.status_code == 200, first.text
    response = client.get(path)
    assert response.status_code == 200
    data = response.json()
    assert first.json()["created"] == 3, first.json()
    assert data["total"] == 3
    by_source = {item["source_type"]: item for item in data["items"]}
    assert set(by_source) == {"page_link", "interaction_observation", "test_case"}
    link = by_source["page_link"]
    assert link["provenance"] == "suggested"
    assert link["steps"][0]["page_id"] == str(page_id)
    assert link["steps"][0]["target_page_id"] == str(target_id)
    assert any(ref.get("element_id") == str(link_id) for ref in link["steps"][0]["evidence"])
    assert any(ref.get("source_type") == "knowledge_graph_edge" for ref in link["steps"][0]["evidence"])
    assert by_source["interaction_observation"]["provenance"] == "observed"
    assert by_source["interaction_observation"]["source_id"] == str(observation_id)
    assert by_source["interaction_observation"]["steps"][0]["observed_state"]["before"]["selected"] is False
    assert by_source["test_case"]["source_id"] == str(case_id)
    assert [step["ordinal"] for step in by_source["test_case"]["steps"]] == [0, 1]
    assert by_source["test_case"]["steps"][1]["page_id"] == str(target_id)

    link_id_saved = link["id"]
    assert client.patch(f"{path}/{link_id_saved}", json={"name": "My verified link", "description": "Reviewed by a person."}).status_code == 200
    reviewed = client.post(f"{path}/{link_id_saved}/review")
    assert reviewed.status_code == 200 and reviewed.json()["reviewed"] is True

    again = client.post(path + "/rebuild")
    assert again.status_code == 200
    assert again.json()["created"] == 0 and again.json()["updated"] == 0 and again.json()["unchanged"] == 3
    saved = client.get(f"{path}/{link_id_saved}").json()
    assert saved["name"] == "My verified link"
    assert saved["description"] == "Reviewed by a person."
    assert saved["reviewed"] is True

    with client.app.state.test_session_factory() as session:
        link = session.get(ObservedElement, link_id)
        link.accessible_name = "About"
        session.commit()
    changed = client.post(path + "/rebuild")
    assert changed.status_code == 200
    assert changed.json()["updated"] == 1
    saved = client.get(f"{path}/{link_id_saved}").json()
    assert saved["name"] == "My verified link"  # user edit survives source changes
    assert saved["reviewed"] is False  # changed evidence requires a fresh review
    assert saved["steps"][0]["control_name"] == "about"

    bad_update = client.patch(f"{path}/{link_id_saved}", json={"evidence": []})
    assert bad_update.status_code == 422
    assert client.patch(f"{path}/{link_id_saved}", json={"description": None}).status_code == 422


def test_invalid_observation_becomes_stale_and_exploration_scopes_filter_pagination(client):
    exploration_id, _page_id, _target_id, _link_id, observation_id, _case_id, _ = _seed(client)
    second_exploration, *_ = _seed(client)
    path = _path(exploration_id)
    other_path = _path(second_exploration)
    assert client.post(path + "/rebuild").status_code == 200
    assert client.post(other_path + "/rebuild").status_code == 200
    page = client.get(path, params={"provenance": "observed", "status": "active", "limit": 1})
    assert page.status_code == 200 and page.json()["total"] == 1
    assert len(page.json()["items"]) == 1
    assert client.get(path, params={"search": "does not exist"}).json()["total"] == 0
    assert client.get(path).json()["total"] == 3
    assert client.get(other_path).json()["total"] == 3

    with client.app.state.test_session_factory() as session:
        session.get(InteractionObservation, observation_id).outcome = "blocked"
        session.commit()
    rebuild = client.post(path + "/rebuild")
    assert rebuild.status_code == 200
    assert rebuild.json()["marked_stale"] == 1
    stale = client.get(path, params={"provenance": "observed", "status": "stale"})
    assert stale.status_code == 200 and stale.json()["total"] == 1


def test_stale_test_case_evidence_is_not_reconstructed(client):
    exploration_id, _page_id, _target_id, _link_id, _observation_id, case_id, _ = _seed(client)
    path = _path(exploration_id)
    assert client.post(path + "/rebuild").status_code == 200
    with client.app.state.test_session_factory() as session:
        case = session.get(TestCase, case_id)
        changed = dict(case.definition)
        scenario = dict(changed["scenario"])
        scenario["relevant_page_ids"] = [str(uuid4())]
        changed["scenario"] = scenario
        case.definition = changed
        case.scenario_version_hash = hashlib.sha256(json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        session.commit()
    rebuilt = client.post(path + "/rebuild")
    assert rebuilt.status_code == 200
    assert rebuilt.json()["marked_stale"] == 1
    saved = client.get(path, params={"provenance": "user_authored", "status": "stale"})
    assert saved.status_code == 200 and saved.json()["total"] == 1
    assert saved.json()["items"][0]["source_id"] == str(case_id)


def test_manual_workflows_and_steps_are_preserved_by_rebuild(client):
    exploration_id, page_id, _target_id, _link_id, _observation_id, _case_id, _ = _seed(client)
    manual_id = uuid4()
    with client.app.state.test_session_factory() as session:
        manual = Workflow(
            id=manual_id, exploration_id=exploration_id, project_id=None,
            identity_hash=hashlib.sha256(f"manual:{manual_id}".encode()).hexdigest(),
            source_type="manual", source_id=str(manual_id), source_hash="h" * 64,
            provenance="user_authored", status="active", name="Keep manual path",
            description="Written by a user.", reviewed=True, evidence=[{"source_type": "page", "source_id": str(page_id)}],
        )
        session.add(manual)
        session.add(WorkflowStep(
            id=uuid4(), workflow_id=manual_id, ordinal=0, step_type="manual_note",
            provenance="user_authored", instruction="Manually documented step.", page_id=page_id,
            page_url="https://workflow-fixture.example/", page_title="Fixture home",
            evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}],
        ))
        session.commit()
    response = client.post(_path(exploration_id) + "/rebuild")
    assert response.status_code == 200
    assert response.json()["preserved_manual"] == 1
    saved = client.get(f"{_path(exploration_id)}/{manual_id}")
    assert saved.status_code == 200
    assert saved.json()["name"] == "Keep manual path"
    assert saved.json()["reviewed"] is True
    assert saved.json()["steps"][0]["instruction"] == "Manually documented step."


def test_project_scope_and_archived_project_blocks_rebuild_without_touching_rows(client):
    exploration_id, _page_id, _target_id, _link_id, _observation_id, _case_id, project_id = _seed(client, project=True)
    assert project_id is not None
    path = _path(exploration_id)
    project_path = f"/api/v1/projects/{project_id}/workflows"
    assert client.post(path + "/rebuild").status_code == 200
    assert client.get(project_path).json()["total"] == 3
    assert client.get(f"/api/v1/projects/{uuid4()}/workflows").status_code == 404
    project_rebuild = client.post(project_path + "/rebuild")
    assert project_rebuild.status_code == 200, project_rebuild.text
    assert project_rebuild.json()["rebuilt_explorations"] == 1
    assert project_rebuild.json()["unchanged"] == 3
    project_flow = client.get(project_path).json()["items"][0]
    project_detail_path = f"{project_path}/{project_flow['id']}"
    assert client.get(project_detail_path).status_code == 200
    assert client.patch(project_detail_path, json={"name": "Project-scoped edit"}).status_code == 200
    assert client.post(project_detail_path + "/review").json()["reviewed"] is True
    assert client.get(f"{_path(uuid4())}/{project_flow['id']}").status_code == 404
    with client.app.state.test_session_factory() as session:
        session.get(ProjectMetadata, project_id).archived_at = datetime.now(UTC)
        before = len(list(session.scalars(select(Workflow).where(Workflow.exploration_id == exploration_id))))
        before_steps = len(list(session.scalars(select(WorkflowStep).join(Workflow, Workflow.id == WorkflowStep.workflow_id).where(Workflow.exploration_id == exploration_id))))
        session.commit()
    response = client.post(path + "/rebuild")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "project_archived"
    project_rebuild = client.post(project_path + "/rebuild")
    assert project_rebuild.status_code == 409
    assert project_rebuild.json()["error"]["code"] == "project_archived"
    with client.app.state.test_session_factory() as session:
        assert len(list(session.scalars(select(Workflow).where(Workflow.exploration_id == exploration_id)))) == before
        assert len(list(session.scalars(select(WorkflowStep).join(Workflow, Workflow.id == WorkflowStep.workflow_id).where(Workflow.exploration_id == exploration_id)))) == before_steps
