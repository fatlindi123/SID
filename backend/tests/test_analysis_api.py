from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage
from app.schemas.analysis import (
    AnalysisEvidence,
    AnalysisResult,
    AnalysisWarning,
    PageSummary,
    WebsitePurpose,
)


def _create_exploration(client: TestClient, *, status: str = "completed", with_page: bool = True):
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            start_url="https://shop.example/",
            status=status,
            max_pages=20,
            max_depth=3,
        )
        session.add(exploration)
        session.flush()
        if with_page:
            session.add(
                ExplorationPage(
                    exploration_id=exploration.id,
                    url=exploration.start_url,
                    title="Shop",
                    depth=0,
                    http_status=200,
                    element_count=0,
                )
            )
        session.commit()
        return exploration.id


def _minimal_result(page_id) -> AnalysisResult:
    evidence = AnalysisEvidence(page_id=page_id, element_ids=[])
    return AnalysisResult(
        website_purpose=WebsitePurpose(
            interpretation="The saved page appears to be a shop.", evidence=[evidence]
        ),
        page_summaries=[
            PageSummary(
                page_id=page_id,
                interpretation="The page is titled Shop.",
                evidence=[evidence],
            )
        ],
        main_navigation=[],
        important_interactive_elements=[],
        forms_and_fields=[],
        potential_workflows=[],
    )


def test_analysis_status_empty_and_start_is_idempotent(client: TestClient) -> None:
    exploration_id = _create_exploration(client)
    url = f"/api/v1/explorations/{exploration_id}/analysis"

    assert client.get(url).json()["status"] == "not_started"
    first = client.post(url)
    second = client.post(url)

    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == second.json()["status"] == "queued"
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["attempt_count"] == second.json()["attempt_count"] == 1
    with client.app.state.test_session_factory() as session:
        assert session.query(ExplorationAnalysis).count() == 1
    assert client.get(f"{url}/results").status_code == 409


def test_analysis_results_are_read_back_from_persistence(client: TestClient) -> None:
    exploration_id = _create_exploration(client)
    with client.app.state.test_session_factory() as session:
        page = session.query(ExplorationPage).filter_by(exploration_id=exploration_id).one()
        result = _minimal_result(page.id)
        result.warnings = [
            AnalysisWarning(collection="potential_workflows", discarded_count=1)
        ]
        analysis = ExplorationAnalysis(
            exploration_id=exploration_id,
            status="completed",
            attempt_count=1,
            model="local-test-model",
            result=result.model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.commit()
        analysis_id = str(analysis.id)

    url = f"/api/v1/explorations/{exploration_id}/analysis"
    status_response = client.get(url)
    result_response = client.get(f"{url}/results")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert result_response.status_code == 200
    assert result_response.json()["id"] == analysis_id
    assert result_response.json()["result"]["website_purpose"]["interpretation"].endswith("a shop.")
    assert result_response.json()["result"]["warnings"] == [
        {"collection": "potential_workflows", "discarded_count": 1}
    ]


def test_older_persisted_result_without_warnings_remains_readable(client: TestClient) -> None:
    exploration_id = _create_exploration(client)
    with client.app.state.test_session_factory() as session:
        page = session.query(ExplorationPage).filter_by(exploration_id=exploration_id).one()
        old_result = _minimal_result(page.id).model_dump(mode="json")
        old_result.pop("warnings")
        session.add(
            ExplorationAnalysis(
                exploration_id=exploration_id,
                status="completed",
                attempt_count=2,
                result=old_result,
                requested_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        session.commit()

    response = client.get(f"/api/v1/explorations/{exploration_id}/analysis/results")

    assert response.status_code == 200
    assert response.json()["result"]["warnings"] == []


def test_rerun_reuses_the_analysis_row_and_increments_attempt(client: TestClient) -> None:
    exploration_id = _create_exploration(client)
    with client.app.state.test_session_factory() as session:
        page = session.query(ExplorationPage).filter_by(exploration_id=exploration_id).one()
        analysis = ExplorationAnalysis(
            exploration_id=exploration_id,
            status="completed",
            attempt_count=1,
            result=_minimal_result(page.id).model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.commit()
        analysis_id = str(analysis.id)

    response = client.post(f"/api/v1/explorations/{exploration_id}/analysis")

    assert response.status_code == 202
    assert response.json()["id"] == analysis_id
    assert response.json()["attempt_count"] == 2
    assert response.json()["status"] == "queued"
    with client.app.state.test_session_factory() as session:
        rows = session.query(ExplorationAnalysis).filter_by(exploration_id=exploration_id).all()
        assert len(rows) == 1
        assert rows[0].result is None


def test_start_requires_completed_exploration_with_saved_pages(client: TestClient) -> None:
    running_id = _create_exploration(client, status="running")
    empty_id = _create_exploration(client, with_page=False)

    running = client.post(f"/api/v1/explorations/{running_id}/analysis")
    empty = client.post(f"/api/v1/explorations/{empty_id}/analysis")

    assert running.status_code == 409
    assert running.json()["error"]["code"] == "exploration_not_completed"
    assert empty.status_code == 409
    assert empty.json()["error"]["code"] == "exploration_has_no_pages"
