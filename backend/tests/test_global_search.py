from datetime import UTC, datetime
from uuid import uuid4

from app.models.exploration import Exploration, ExplorationPage
from app.models.project import Project, ProjectMetadata
from app.models.report import GeneratedReport
from app.models.test_case import TestCase
from app.models.workflow import Workflow


def _seed_search_records(client):
    now = datetime.now(UTC)
    exploration_id = uuid4()
    page_id = uuid4()
    project_id = uuid4()
    case_id = uuid4()
    workflow_id = uuid4()
    report_id = uuid4()
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url="https://alpha.example.test/",
            status="completed",
            max_pages=10,
            max_depth=2,
        )
        session.add_all(
            [
                exploration,
                ExplorationPage(
                    id=page_id,
                    exploration_id=exploration_id,
                    url="https://alpha.example.test/login",
                    title="Alpha Login",
                    depth=0,
                    http_status=200,
                ),
                Project(
                    id=project_id,
                    name="Alpha Project",
                    target_url="https://alpha.example.test/",
                ),
                ProjectMetadata(project_id=project_id, description="", environment="staging"),
                TestCase(
                    id=case_id,
                    exploration_id=exploration_id,
                    source="user_created",
                    identity_hash="a" * 64,
                    source_analysis_id=None,
                    analysis_version_hash=None,
                    scenario_version_hash="b" * 64,
                    name="Alpha Sign in",
                    description="Sign in form is displayed",
                    definition={"schema_version": 1},
                    is_current=True,
                ),
                Workflow(
                    id=workflow_id,
                    exploration_id=exploration_id,
                    project_id=None,
                    identity_hash="c" * 64,
                    source_type="manual",
                    source_id="fixture",
                    source_hash="d" * 64,
                    provenance="user_authored",
                    status="active",
                    name="Alpha checkout flow",
                    description="Saved workflow",
                    reviewed=False,
                    evidence=[],
                ),
                GeneratedReport(
                    id=report_id,
                    report_type="exploration",
                    project_id=None,
                    exploration_id=exploration_id,
                    scope_label="Alpha report",
                    status="completed",
                    generated_at=now,
                    completed_at=now,
                    filters={},
                    snapshot={"schema_version": 1},
                ),
            ]
        )
        session.commit()
    return {
        "exploration": exploration_id,
        "page": page_id,
        "project": project_id,
        "test_case": case_id,
        "workflow": workflow_id,
        "report": report_id,
    }


def test_global_search_finds_saved_entities_and_returns_valid_destinations(client):
    ids = _seed_search_records(client)

    response = client.get("/api/v1/search", params={"q": "aLpHa"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert {item["entity_type"] for item in payload["results"]} == set(ids)
    by_type = {item["entity_type"]: item for item in payload["results"]}
    assert by_type["project"]["destination"] == {
        "workspace": "Projects",
        "project_id": str(ids["project"]),
        "exploration_id": None,
        "page_id": None,
        "report_id": None,
    }
    assert by_type["page"]["destination"]["exploration_id"] == str(ids["exploration"])
    assert by_type["page"]["destination"]["page_id"] == str(ids["page"])
    assert by_type["test_case"]["destination"]["workspace"] == "Test Cases"
    assert by_type["workflow"]["destination"]["workspace"] == "Workflow Intelligence"
    assert by_type["report"]["destination"]["report_id"] == str(ids["report"])


def test_global_search_validates_query_and_escapes_like_wildcards(client):
    _seed_search_records(client)

    assert client.get("/api/v1/search", params={"q": "a"}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "   "}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "x" * 101}).status_code == 422
    assert client.get("/api/v1/search", params={"q": "%%"}).json()["results"] == []


def test_search_redacts_sensitive_text_and_does_not_match_url_query_values(client):
    _seed_search_records(client)
    project_id = uuid4()
    with client.app.state.test_session_factory() as session:
        session.add(
            Project(
                id=project_id,
                name="Alpha password=do-not-return-this",
                target_url="https://private.example.test/?token=do-not-return-this",
            )
        )
        session.commit()

    response = client.get("/api/v1/search", params={"q": "do-not-return-this"})

    assert response.status_code == 200
    assert "do-not-return-this" not in response.text
    assert "query" not in response.json()
    assert all(item["id"] != str(project_id) for item in response.json()["results"])


def test_global_search_is_bounded_to_twenty_results_and_four_per_category(client):
    now = datetime.now(UTC)
    with client.app.state.test_session_factory() as session:
        for index in range(5):
            session.add(Project(
                id=uuid4(), name=f"Bounded search project {index}",
                target_url=f"https://bounded-{index}.example.test/",
            ))
            exploration = Exploration(
                id=uuid4(), start_url=f"https://bounded-{index}.example.test/", status="completed",
                max_pages=1, max_depth=0,
            )
            session.add(exploration)
            session.flush()
            session.add(ExplorationPage(
                id=uuid4(), exploration_id=exploration.id,
                url=f"https://bounded-{index}.example.test/page", title=f"Bounded saved page {index}",
                depth=0, http_status=200,
            ))
            session.add(TestCase(
                id=uuid4(), exploration_id=exploration.id, source="user_created",
                identity_hash=f"{index:064x}", source_analysis_id=None, analysis_version_hash=None,
                scenario_version_hash="e" * 64, name=f"Bounded test case {index}", description="",
                definition={"schema_version": 1}, is_current=True,
            ))
            session.add(Workflow(
                id=uuid4(), exploration_id=exploration.id, project_id=None,
                identity_hash=f"{index + 10:064x}", source_type="manual",
                source_id=f"fixture-{index}", source_hash="f" * 64,
                provenance="user_authored", status="active", name=f"Bounded workflow {index}",
                description="", reviewed=False, evidence=[],
            ))
            session.add(GeneratedReport(
                id=uuid4(), report_type="exploration", project_id=None,
                exploration_id=exploration.id, scope_label=f"Bounded report {index}",
                status="completed", generated_at=now, completed_at=now,
                filters={}, snapshot={"schema_version": 1},
            ))
        session.commit()

    response = client.get("/api/v1/search", params={"q": "bounded"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) <= 20
    counts = {}
    for item in results:
        counts[item["entity_type"]] = counts.get(item["entity_type"], 0) + 1
    assert max(counts.values()) <= 4
