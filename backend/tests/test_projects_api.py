from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.models.exploration import Exploration
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.project import ProjectExploration
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep


def _create_project(client, **overrides):
    payload = {
        "name": "Documentation project",
        "target_url": "https://docs.example.test/",
        "description": "Verified product docs",
        "environment": "staging",
    }
    payload.update(overrides)
    return client.post("/api/v1/projects", json=payload)


def _seed_exploration(client, url: str, *, status: str = "completed") -> Exploration:
    exploration = Exploration(
        id=uuid4(),
        start_url=url,
        status=status,
        max_pages=10,
        max_depth=2,
        pages_discovered=3,
        elements_discovered=12,
        event_count=5,
        completed_at=datetime.now(UTC) if status == "completed" else None,
    )
    with client.app.state.test_session_factory() as session:
        session.add(exploration)
        session.commit()
        session.refresh(exploration)
    return exploration


def test_project_crud_validation_search_and_pagination(client):
    first = _create_project(client, name="  Checkout  ", target_url="https://shop.example.test")
    second = _create_project(client, name="Account portal", target_url="https://account.example.test")
    third = _create_project(client, name="Checkout docs", target_url="https://docs.example.test")
    assert first.status_code == 201, first.text
    assert first.json()["name"] == "Checkout"
    assert first.json()["description"] == "Verified product docs"
    assert first.json()["environment"] == "staging"
    assert first.json()["stats"]["exploration_count"] == 0
    assert second.status_code == third.status_code == 201

    searched = client.get("/api/v1/projects", params={"search": "checkout", "limit": 1, "offset": 0})
    assert searched.status_code == 200
    assert searched.json()["total"] == 2
    assert len(searched.json()["items"]) == 1
    next_page = client.get("/api/v1/projects", params={"search": "checkout", "limit": 1, "offset": 1}).json()
    assert len(next_page["items"]) == 1
    assert next_page["items"][0]["id"] != searched.json()["items"][0]["id"]

    project_id = first.json()["id"]
    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"description": "  Public checkout flow  ", "environment": "production"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["description"] == "Public checkout flow"
    assert updated.json()["environment"] == "production"
    assert client.get(f"/api/v1/projects/{project_id}").json()["target_url"] == "https://shop.example.test"

    for bad_url in ("file:///etc/passwd", "https://user:secret@example.test", "https://example.test:bad"):
        assert _create_project(client, target_url=bad_url).status_code == 422
    assert client.post("/api/v1/projects", json={"name": "   ", "target_url": "https://example.test"}).status_code == 422
    assert client.get("/api/v1/projects", params={"limit": 0}).status_code == 422
    assert client.patch(f"/api/v1/projects/{project_id}", json={}).status_code == 422

    archived = client.post(f"/api/v1/projects/{second.json()['id']}/archive")
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert client.get("/api/v1/projects", params={"state": "active"}).json()["total"] == 2
    assert client.get("/api/v1/projects", params={"state": "archived"}).json()["total"] == 1
    assert client.post(f"/api/v1/projects/{second.json()['id']}/restore").json()["archived"] is False
    assert client.get("/api/v1/projects/not-a-uuid").status_code == 422
    assert client.get(f"/api/v1/projects/{uuid4()}").status_code == 404


def test_project_scoping_association_summaries_and_single_assignment(client):
    first_exploration = _seed_exploration(client, "https://shop.example.test/home")
    second_exploration = _seed_exploration(client, "https://shop.example.test/cart", status="running")
    first = _create_project(client, name="Shop")
    second = _create_project(client, name="Other")
    first_id, second_id = first.json()["id"], second.json()["id"]

    available = client.get("/api/v1/projects/available-explorations", params={"search": "shop.example", "limit": 1})
    assert available.status_code == 200 and available.json()["total"] == 2
    assert len(available.json()["items"]) == 1
    assert client.put(f"/api/v1/projects/{first_id}/explorations/{first_exploration.id}").status_code == 201
    assert client.put(f"/api/v1/projects/{first_id}/explorations/{first_exploration.id}").status_code == 201
    conflict = client.put(f"/api/v1/projects/{second_id}/explorations/{first_exploration.id}")
    assert conflict.status_code == 409
    assert client.put(f"/api/v1/projects/{first_id}/explorations/{uuid4()}").status_code == 404

    case_id, run_id = uuid4(), uuid4()
    node_id, other_node_id, edge_id = uuid4(), uuid4(), uuid4()
    with client.app.state.test_session_factory() as session:
        session.add_all(
            [
                TestCase(
                    id=case_id,
                    exploration_id=first_exploration.id,
                    source="user_created",
                    identity_hash="a" * 64,
                    source_analysis_id=None,
                    analysis_version_hash=None,
                    scenario_version_hash="b" * 64,
                    name="Home loads",
                    description="Open the home page",
                    definition={"schema_version": 1, "execution_kind": "workflow", "scenario": {}, "steps": []},
                    is_current=True,
                ),
                TestRun(
                    id=run_id,
                    exploration_id=first_exploration.id,
                    analysis_id=None,
                    scenario_index=-1,
                    test_case_id=case_id,
                    scenario={"title": "Immutable saved run"},
                    status="passed",
                ),
                KnowledgeGraphNode(
                    id=node_id,
                    exploration_id=first_exploration.id,
                    node_type="page",
                    source_key="page-home",
                    label="Home",
                    page_id=None,
                    provenance="verified",
                    evidence=[{"source_type": "page", "source_id": str(uuid4()), "page_id": None, "element_id": None}],
                    properties={},
                ),
                KnowledgeGraphNode(
                    id=other_node_id,
                    exploration_id=first_exploration.id,
                    node_type="ui_element",
                    source_key="button-1",
                    label="Continue",
                    page_id=None,
                    provenance="verified",
                    evidence=[{"source_type": "element", "source_id": str(uuid4()), "page_id": None, "element_id": None}],
                    properties={},
                ),
                KnowledgeGraphEdge(
                    id=edge_id,
                    exploration_id=first_exploration.id,
                    source_node_id=node_id,
                    target_node_id=other_node_id,
                    edge_type="contains_element",
                    provenance="verified",
                    evidence=[{"source_type": "element", "source_id": str(uuid4()), "page_id": None, "element_id": None}],
                    properties={},
                ),
            ]
        )
        session.flush()
        session.add(TestRunStep(
            id=uuid4(), test_run_id=run_id, ordinal=0, instruction="Open the page", action={},
            expected_evidence={}, status="passed",
        ))
        session.commit()

    assert client.put(f"/api/v1/projects/{first_id}/explorations/{second_exploration.id}").status_code == 201
    detail = client.get(f"/api/v1/projects/{first_id}").json()
    assert detail["stats"]["exploration_count"] == 2
    assert detail["stats"]["active_exploration_count"] == 1
    assert detail["stats"]["completed_exploration_count"] == 1
    assert detail["stats"]["test_case_count"] == 1
    assert detail["stats"]["test_run_count"] == 1
    assert detail["stats"]["passed_run_count"] == 1
    assert detail["stats"]["knowledge_graph_exploration_count"] == 1

    explorations = client.get(f"/api/v1/projects/{first_id}/explorations", params={"search": "/home"}).json()
    assert explorations["total"] == 1
    assert explorations["items"][0]["id"] == str(first_exploration.id)
    assert explorations["items"][0]["knowledge_graph_available"] is True
    cases = client.get(f"/api/v1/projects/{first_id}/test-cases", params={"search": "loads", "case_status": "active"}).json()
    assert cases["total"] == 1 and cases["items"][0]["id"] == str(case_id)
    runs = client.get(f"/api/v1/projects/{first_id}/run-history", params={"run_status": "passed"}).json()
    assert runs["total"] == 1 and runs["items"][0]["step_count"] == 1
    graphs = client.get(f"/api/v1/projects/{first_id}/knowledge-graphs").json()
    assert graphs["total"] == 2
    assert sum(item["available"] for item in graphs["items"]) == 1
    assert client.get(f"/api/v1/projects/{second_id}/explorations").json()["total"] == 0

    assert client.delete(f"/api/v1/projects/{first_id}/explorations/{first_exploration.id}").status_code == 204
    assert client.get(f"/api/v1/projects/{first_id}/explorations").json()["total"] == 1
    assert client.delete(f"/api/v1/projects/{first_id}/explorations/{first_exploration.id}").status_code == 404


def test_archived_project_blocks_new_assignments_and_test_runs_without_deleting_history(client):
    exploration = _seed_exploration(client, "https://archived.example.test/", status="completed")
    later_exploration = _seed_exploration(client, "https://new.example.test/", status="completed")
    case_id, run_id, step_id = uuid4(), uuid4(), uuid4()
    with client.app.state.test_session_factory() as session:
        session.add(
            TestCase(
                id=case_id,
                exploration_id=exploration.id,
                source="user_created",
                identity_hash="c" * 64,
                source_analysis_id=None,
                analysis_version_hash=None,
                scenario_version_hash="d" * 64,
                name="Archived project case",
                description="Retained after archive",
                definition={"schema_version": 1, "execution_kind": "workflow", "scenario": {}, "steps": []},
                is_current=True,
            )
        )
        session.add(
            TestRun(
                id=run_id,
                exploration_id=exploration.id,
                analysis_id=None,
                scenario_index=-1,
                test_case_id=case_id,
                scenario={"title": "Historical run snapshot"},
                status="passed",
            )
        )
        session.flush()
        session.add(TestRunStep(
            id=step_id, test_run_id=run_id, ordinal=0, instruction="Verified step", action={},
            expected_evidence={}, status="passed",
        ))
        session.commit()

    created = _create_project(client, name="Archive me")
    project_id = created.json()["id"]
    assert client.put(f"/api/v1/projects/{project_id}/explorations/{exploration.id}").status_code == 201
    archived = client.post(f"/api/v1/projects/{project_id}/archive")
    assert archived.status_code == 200 and archived.json()["archived"] is True
    assert archived.json()["stats"]["test_run_count"] == 1

    association = client.put(f"/api/v1/projects/{project_id}/explorations/{later_exploration.id}")
    assert association.status_code == 409
    start_run = client.post(
        f"/api/v1/explorations/{exploration.id}/test-runs",
        json={"test_case_id": str(case_id)},
    )
    assert start_run.status_code == 409
    assert start_run.json()["error"]["code"] == "project_archived"
    rerun = client.post(f"/api/v1/explorations/{exploration.id}/test-cases/{case_id}/runs")
    assert rerun.status_code == 409
    assert rerun.json()["error"]["code"] == "project_archived"

    with client.app.state.test_session_factory() as session:
        assert session.get(TestCase, case_id) is not None
        run = session.get(TestRun, run_id)
        assert run is not None and run.scenario == {"title": "Historical run snapshot"}
        assert session.get(TestRunStep, step_id) is not None
        assert session.get(ProjectExploration, exploration.id).project_id == UUID(project_id)

    assert client.post(f"/api/v1/projects/{project_id}/restore").json()["archived"] is False
    assert client.put(f"/api/v1/projects/{project_id}/explorations/{later_exploration.id}").status_code == 201
