from datetime import UTC, datetime
from uuid import UUID
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage, ObservedElement
from app.models.test_case import TestCase
from app.models.test_execution import TestRun
from app.schemas.analysis import (
    AnalysisEvidence,
    AnalysisResult,
    PageSummary,
    WebsitePurpose,
    WorkflowStep,
    WorkflowSuggestion,
)


def _create_completed_scenario(client: TestClient, instruction: str = "Verify the Continue button is visible."):
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            start_url="https://shop.example/",
            status="completed",
            max_pages=10,
            max_depth=2,
            pages_discovered=1,
        )
        page = ExplorationPage(
            exploration=exploration,
            url=exploration.start_url,
            title="Example Shop",
            depth=0,
            http_status=200,
            element_count=1,
        )
        session.add(exploration)
        session.flush()
        element = ObservedElement(
            page=page,
            ordinal=0,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Continue",
            selector="button#continue",
            attributes={"id": "continue", "type": "button"},
        )
        session.add(element)
        session.flush()
        reference = AnalysisEvidence(page_id=page.id, element_ids=[element.id])
        workflow = WorkflowSuggestion(
            title="Verify the Continue button",
            objective="Check that the primary action is available.",
            relevant_page_ids=[page.id],
            steps=[WorkflowStep(instruction=instruction, evidence=[reference])],
            expected_outcome="The saved Continue button is visible.",
        )
        result = AnalysisResult(
            website_purpose=WebsitePurpose(
                interpretation="The page presents a primary action.", evidence=[reference]
            ),
            page_summaries=[
                PageSummary(
                    page_id=page.id,
                    interpretation="The page contains a Continue button.",
                    evidence=[reference],
                )
            ],
            main_navigation=[],
            important_interactive_elements=[],
            forms_and_fields=[],
            potential_workflows=[workflow],
        )
        analysis = ExplorationAnalysis(
            exploration_id=exploration.id,
            status="completed",
            attempt_count=1,
            result=result.model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.commit()
        return exploration.id


def _create_saucedemo_login_evidence(client: TestClient):
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            start_url="https://www.saucedemo.com/",
            status="completed",
            max_pages=10,
            max_depth=2,
            pages_discovered=1,
        )
        page = ExplorationPage(
            exploration=exploration,
            url=exploration.start_url,
            title="Swag Labs",
            depth=0,
            http_status=200,
            element_count=1,
        )
        session.add(exploration)
        session.flush()
        login = ObservedElement(
            page=page,
            ordinal=0,
            kind="input",
            tag_name="input",
            role="button",
            accessible_name="Login",
            selector="input#login-button",
            attributes={"id": "login-button", "type": "submit"},
        )
        session.add(login)
        session.flush()
        reference = AnalysisEvidence(page_id=page.id, element_ids=[login.id])
        result = AnalysisResult(
            website_purpose=WebsitePurpose(
                interpretation="A saved SauceDemo login page.", evidence=[reference]
            ),
            page_summaries=[
                PageSummary(
                    page_id=page.id,
                    interpretation="The saved page contains a Login button.",
                    evidence=[reference],
                )
            ],
            main_navigation=[],
            important_interactive_elements=[],
            forms_and_fields=[],
            potential_workflows=[],
        )
        analysis = ExplorationAnalysis(
            exploration_id=exploration.id,
            status="completed",
            attempt_count=8,
            model="saved-model",
            result=result.model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.commit()
        return exploration.id, page.id, login.id, analysis.id, analysis.result.copy()


def _create_empty_exploration(client: TestClient):
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            start_url="https://shop.example/",
            status="completed",
            max_pages=10,
            max_depth=2,
        )
        session.add(exploration)
        session.flush()
        session.add(
            ExplorationPage(
                exploration_id=exploration.id,
                url=exploration.start_url,
                title="Shop",
                depth=0,
                http_status=200,
            )
        )
        session.commit()
        return exploration.id


def test_valid_ai_scenario_is_listed_queued_and_persisted(client: TestClient) -> None:
    exploration_id = _create_completed_scenario(client)
    base = f"/api/v1/explorations/{exploration_id}"

    cases_response = client.get(base + "/test-cases")
    assert cases_response.status_code == 200
    cases = cases_response.json()
    assert cases["analysis_status"] == "completed"
    assert cases["cases"][0]["source"] == "ai_generated"
    case = cases["cases"][0]

    response = client.post(base + "/test-runs", json={"test_case_id": case["id"]})
    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "pending"
    assert run["test_case_id"] == case["id"]
    assert run["scenario"]["scenario"]["title"] == "Verify the Continue button"
    assert run["scenario_index"] == -1
    assert run["steps"][0]["action"]["kind"] == "assert_visible"
    assert [str(run_id) for run_id in client.app.state.fake_test_execution_manager.queued] == [run["id"]]

    detail = client.get(base + "/test-runs/" + run["id"])
    history = client.get(base + "/test-runs")
    assert detail.status_code == history.status_code == 200
    assert detail.json()["steps"][0]["status"] == "pending"
    assert len(history.json()) == 1

    second_run = client.post(base + "/test-runs", json={"test_case_id": case["id"]})
    assert second_run.status_code == 202
    first_page = client.get(base + "/test-runs?limit=1&offset=0")
    second_page = client.get(base + "/test-runs?limit=1&offset=1")
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.json()[0]["id"] != second_page.json()[0]["id"]
    assert client.get(base + "/test-runs?limit=101").status_code == 422
    with client.app.state.test_session_factory() as session:
        persisted = session.scalar(select(TestRun).where(TestRun.id == UUID(run["id"])))
        assert persisted is not None
        assert persisted.status == "pending"


def test_unsupported_scenario_is_visible_but_cannot_be_queued(client: TestClient) -> None:
    exploration_id = _create_completed_scenario(client, "Enter a username and submit the login form.")
    base = f"/api/v1/explorations/{exploration_id}"

    scenario = client.get(base + "/test-scenarios").json()[0]
    cases = client.get(base + "/test-cases").json()["cases"]
    response = client.post(
        base + "/test-runs",
        json={"test_case_id": cases[0]["id"]} if cases else {"test_case_id": str(uuid4())},
    )

    assert scenario["executable"] is False
    assert "unsupported action" in scenario["unavailable_reason"]
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "test_case_not_found"
    with client.app.state.test_session_factory() as session:
        assert session.query(TestRun).count() == 0


def test_unknown_test_case_id_is_rejected_without_creating_a_run(client: TestClient) -> None:
    exploration_id = _create_completed_scenario(client)
    response = client.post(
        f"/api/v1/explorations/{exploration_id}/test-runs",
        json={"test_case_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "test_case_not_found"


@pytest.mark.parametrize("analysis_status", ["not_started", "queued", "running", "failed"])
def test_case_list_reports_analysis_state_instead_of_empty_completion(
    client: TestClient, analysis_status: str
) -> None:
    exploration_id = _create_empty_exploration(client)
    if analysis_status != "not_started":
        with client.app.state.test_session_factory() as session:
            session.add(
                ExplorationAnalysis(
                    exploration_id=exploration_id,
                    status=analysis_status,
                    attempt_count=2,
                    error_message="Provider unavailable." if analysis_status == "failed" else None,
                    result=None,
                    requested_at=datetime.now(UTC),
                )
            )
            session.commit()

    response = client.get(f"/api/v1/explorations/{exploration_id}/test-cases")

    assert response.status_code == 200
    assert response.json()["analysis_status"] == analysis_status
    assert response.json()["analysis_attempt_count"] == (0 if analysis_status == "not_started" else 2)
    assert response.json()["cases"] == []
    if analysis_status == "failed":
        assert response.json()["analysis_error"] == "Provider unavailable."


def test_completed_analysis_without_a_result_is_not_returned_as_empty_cases(client: TestClient) -> None:
    exploration_id = _create_empty_exploration(client)
    with client.app.state.test_session_factory() as session:
        session.add(
            ExplorationAnalysis(
                exploration_id=exploration_id,
                status="completed",
                attempt_count=1,
                result=None,
                requested_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        session.commit()

    response = client.get(f"/api/v1/explorations/{exploration_id}/test-cases")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "analysis_result_invalid"


def test_manual_saucedemo_smoke_test_uses_saved_evidence_and_preserves_analysis(client: TestClient) -> None:
    exploration_id, page_id, login_id, analysis_id, saved_analysis_result = _create_saucedemo_login_evidence(client)
    base = f"/api/v1/explorations/{exploration_id}"

    response = client.post(
        base + "/test-runs",
        json={"manual_smoke_test": {"page_id": str(page_id), "login_button_element_id": str(login_id)}},
    )

    assert response.status_code == 202
    run = response.json()
    assert run["analysis_id"] == str(analysis_id)
    assert run["scenario_index"] == -1
    assert run["scenario"]["source"] == "user_created"
    assert run["scenario"]["scenario"]["source"] == "manual"
    assert run["scenario"]["scenario"]["title"] == "Manual SauceDemo login page smoke test"
    assert run["test_case_id"]
    assert [step["action"]["kind"] for step in run["steps"]] == ["navigate", "assert_visible"]
    assert run["steps"][1]["action"]["selector"] == "input#login-button"
    assert run["steps"][1]["action"]["element_id"] == str(login_id)
    assert [str(run_id) for run_id in client.app.state.fake_test_execution_manager.queued] == [run["id"]]

    with client.app.state.test_session_factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        assert analysis is not None
        assert analysis.attempt_count == 8
        assert analysis.result == saved_analysis_result
        persisted = session.get(TestRun, UUID(run["id"]))
        assert persisted is not None and persisted.status == "pending"
        case = session.get(TestCase, UUID(run["test_case_id"]))
        assert case is not None and case.source == "user_created"

    selected_case_run = client.post(
        base + "/test-runs",
        json={"test_case_id": run["test_case_id"]},
    )
    assert selected_case_run.status_code == 202
    assert selected_case_run.json()["test_case_id"] == run["test_case_id"]
    assert selected_case_run.json()["analysis_id"] == str(analysis_id)

    with client.app.state.test_session_factory() as session:
        alternate_login = ObservedElement(
            page_id=page_id,
            ordinal=1,
            kind="input",
            tag_name="input",
            role="button",
            accessible_name="Login",
            selector="input#login-button-secondary",
            attributes={"id": "login-button-secondary", "type": "submit"},
        )
        session.add(alternate_login)
        session.commit()
        alternate_login_id = alternate_login.id
    alternate_case_run = client.post(
        base + "/test-runs",
        json={"manual_smoke_test": {"page_id": str(page_id), "login_button_element_id": str(alternate_login_id)}},
    )
    assert alternate_case_run.status_code == 202
    assert alternate_case_run.json()["test_case_id"] != run["test_case_id"]

    repeated = client.post(
        base + "/test-runs",
        json={"manual_smoke_test": {"page_id": str(page_id), "login_button_element_id": str(login_id)}},
    )
    assert repeated.status_code == 202
    assert repeated.json()["test_case_id"] == run["test_case_id"]


def test_manual_saucedemo_smoke_test_rejects_unpersisted_element_id(client: TestClient) -> None:
    exploration_id, page_id, _login_id, _analysis_id, _ = _create_saucedemo_login_evidence(client)
    response = client.post(
        f"/api/v1/explorations/{exploration_id}/test-runs",
        json={"manual_smoke_test": {"page_id": str(page_id), "login_button_element_id": str(uuid4())}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "manual_scenario_not_executable"
    with client.app.state.test_session_factory() as session:
        assert session.query(TestRun).count() == 0


def _manual_navigation_scenario(page_id: UUID, title: str = "Open the saved page") -> dict:
    return {
        "title": title,
        "objective": "Check whether the saved page loads.",
        "relevant_page_ids": [str(page_id)],
        "steps": [{
            "instruction": "Navigate to the saved page.",
            "evidence": [{"page_id": str(page_id), "element_ids": []}],
        }],
        "expected_outcome": "The saved page is displayed.",
    }


def test_user_case_crud_duplicate_filters_and_soft_delete_preserve_run_history(client: TestClient) -> None:
    exploration_id = _create_empty_exploration(client)
    base = f"/api/v1/explorations/{exploration_id}"
    with client.app.state.test_session_factory() as session:
        page_id = session.scalar(select(ExplorationPage.id).where(ExplorationPage.exploration_id == exploration_id))

    created = client.post(base + "/test-cases", json={
        "name": "Navigation smoke",
        "description": "A saved page-only check.",
        "scenario": _manual_navigation_scenario(page_id),
    })
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["source"] == "user_created"
    assert case["status"] == "active"
    assert case["name"] == "Navigation smoke"
    assert case["description"] == "A saved page-only check."
    assert case["exploration_id"] == str(exploration_id)
    assert case["definition"]["scenario"]["title"] == "Open the saved page"

    duplicate = client.post(base + f"/test-cases/{case['id']}/duplicate")
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["id"] != case["id"]
    assert duplicate.json()["identity_hash"] != case["identity_hash"]

    page_one = client.get(base + "/test-cases?source=user_created&status=active&search=Navigation&limit=1&offset=0")
    page_two = client.get(base + "/test-cases?source=user_created&status=active&search=Navigation&limit=1&offset=1")
    assert page_one.status_code == page_two.status_code == 200
    assert page_one.json()["total"] == 2
    assert len(page_two.json()["cases"]) == 1
    paged_ids = {row["id"] for row in page_one.json()["cases"] + page_two.json()["cases"]}
    assert paged_ids == {case["id"], duplicate.json()["id"]}
    assert client.get(base + "/test-cases?limit=101").status_code == 422

    run_response = client.post(base + f"/test-cases/{case['id']}/runs")
    assert run_response.status_code == 202, run_response.text
    run = run_response.json()
    assert run["analysis_id"] is None
    assert run["test_case_id"] == case["id"]
    saved_snapshot = run["scenario"].copy()
    assert run["scenario"]["scenario"]["title"] == "Open the saved page"

    update = client.patch(base + f"/test-cases/{case['id']}", json={
        "name": "Navigation smoke revised",
        "description": "Updated metadata.",
        "scenario": _manual_navigation_scenario(page_id, "Open the page after edit"),
    })
    assert update.status_code == 200, update.text
    assert update.json()["name"] == "Navigation smoke revised"
    assert update.json()["definition"]["scenario"]["title"] == "Open the page after edit"
    detail = client.get(base + f"/test-runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["scenario"] == saved_snapshot

    deleted = client.delete(base + f"/test-cases/{case['id']}")
    assert deleted.status_code == 204
    assert client.get(base + f"/test-cases/{case['id']}").json()["status"] == "deleted"
    deleted_list = client.get(base + "/test-cases?status=deleted&source=user_created")
    assert [row["id"] for row in deleted_list.json()["cases"]] == [case["id"]]
    history = client.get(base + f"/test-run-history?test_case_id={case['id']}&limit=5&offset=0")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["id"] == run["id"]
    with client.app.state.test_session_factory() as session:
        persisted = session.get(TestRun, UUID(run["id"]))
        assert persisted is not None and len(persisted.steps) == 1
        assert persisted.scenario == saved_snapshot
        assert session.get(TestCase, UUID(case["id"])).deleted_at is not None


def test_manual_case_rejects_unknown_evidence_and_ai_scenario_edits(client: TestClient) -> None:
    exploration_id = _create_completed_scenario(client)
    base = f"/api/v1/explorations/{exploration_id}"
    with client.app.state.test_session_factory() as session:
        page_id = session.scalar(select(ExplorationPage.id).where(ExplorationPage.exploration_id == exploration_id))
    invalid = _manual_navigation_scenario(page_id)
    invalid["steps"][0]["evidence"][0]["page_id"] = str(uuid4())
    response = client.post(base + "/test-cases", json={
        "name": "Invalid case",
        "scenario": invalid,
    })
    assert response.status_code == 422

    case = client.get(base + "/test-cases").json()["cases"][0]
    rejected = client.patch(base + f"/test-cases/{case['id']}", json={
        "scenario": _manual_navigation_scenario(page_id),
    })
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "ai_case_scenario_read_only"
    metadata = client.patch(base + f"/test-cases/{case['id']}", json={
        "name": "Reviewed AI case",
        "description": "The validated generated scenario is unchanged.",
    })
    assert metadata.status_code == 200
    assert metadata.json()["name"] == "Reviewed AI case"
    assert metadata.json()["definition"] == case["definition"]


def test_user_case_rerun_uses_current_snapshot_and_paginated_history(client: TestClient) -> None:
    exploration_id = _create_empty_exploration(client)
    base = f"/api/v1/explorations/{exploration_id}"
    with client.app.state.test_session_factory() as session:
        page_id = session.scalar(select(ExplorationPage.id).where(ExplorationPage.exploration_id == exploration_id))
    created = client.post(base + "/test-cases", json={
        "name": "Original name",
        "scenario": _manual_navigation_scenario(page_id),
    })
    assert created.status_code == 201, created.text
    case = created.json()
    client.patch(base + f"/test-cases/{case['id']}", json={
        "name": "Current name",
        "scenario": _manual_navigation_scenario(page_id, "Current scenario"),
    })
    rerun = client.post(base + f"/test-cases/{case['id']}/runs")
    assert rerun.status_code == 202, rerun.text
    run = rerun.json()
    assert run["scenario"]["scenario"]["title"] == "Current scenario"
    assert run["scenario"]["test_case_id"] == case["id"]
    assert client.app.state.fake_test_execution_manager.queued == [UUID(run["id"])]

    history = client.get(base + "/test-run-history?limit=1&offset=0")
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert len(history.json()["items"]) == 1
    assert history.json()["limit"] == 1
    assert history.json()["active_run_count"] == 1
