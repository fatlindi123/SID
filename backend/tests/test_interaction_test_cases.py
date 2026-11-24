import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select

from app.models.exploration import Exploration, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.models.test_case import TestCase
from app.models.test_execution import TestRun
from app.services.test_case_service import compile_test_case_definition
from app.services.test_interactions import load_interaction_observations


def _probe_key(action_type: str, element: ObservedElement) -> str:
    source = "\0".join(
        (
            action_type,
            str(element.ordinal),
            str(element.tag_name),
            str(element.role or ""),
            str(element.accessible_name or ""),
            str(element.selector or ""),
        )
    )
    return hashlib.sha256(source.encode()).hexdigest()


def _seed_observation(client, *, outcome="observed", stale_name=None, action_type="tab", inside_form=False):
    exploration_id, page_id, element_id, observation_id = uuid4(), uuid4(), uuid4(), uuid4()
    page_url = "https://fixture.example/"
    with client.app.state.test_session_factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url=page_url,
            status="completed",
            max_pages=5,
            max_depth=1,
        )
        page = ExplorationPage(
            id=page_id,
            exploration=exploration,
            url=page_url,
            title="Interaction Fixture",
            depth=0,
            http_status=200,
        )
        role = "tab" if action_type == "tab" else "button"
        name = stale_name or ("Details" if action_type == "tab" else "Help")
        element_id_attr = "details-tab" if action_type == "tab" else "help-toggle"
        control_id = "details-panel" if action_type == "tab" else "help-panel"
        state_attribute = "aria-selected" if action_type == "tab" else "aria-expanded"
        element_attributes = {
            "id": element_id_attr,
            "type": "button",
            state_attribute: "false",
            "aria-controls": control_id,
            "inside_form": inside_form,
            "controls_valid": True,
        }
        if action_type == "tab":
            element_attributes.update({"role": "tab", "inside_tablist": True})
        element = ObservedElement(
            id=element_id,
            page=page,
            ordinal=0,
            kind="control",
            tag_name="button",
            role=role,
            accessible_name="Details" if action_type == "tab" else "Help",
            selector=f"button#{element_id_attr}",
            attributes=element_attributes,
        )
        session.add_all([exploration, page, element])
        session.flush()
        before = {
            "url": page_url,
            "title": "Interaction Fixture",
            "target": {"tag_name": "button", "role": role, "name": name, "selected": False if action_type == "tab" else None, "expanded": False if action_type == "disclosure" else None},
            "visible_elements": [],
            "visible_changes": {"added": [], "removed": [], "state_changes": []},
        }
        after = {
            "url": page_url,
            "title": "Interaction Fixture",
            "target": {"tag_name": "button", "role": role, "name": name, "selected": True if action_type == "tab" else None, "expanded": True if action_type == "disclosure" else None},
            "visible_elements": [],
            "visible_changes": {"added": [], "removed": [], "state_changes": []},
        }
        observation = InteractionObservation(
            id=observation_id,
            exploration_id=exploration_id,
            page_id=page_id,
            element_ordinal=0,
            probe_key=_probe_key(action_type, element),
            action_type=action_type,
            outcome=outcome,
            reason=None if outcome == "observed" else "probe_not_completed",
            before_evidence=before,
            after_evidence=after,
            duration_ms=28,
        )
        session.add(observation)
        session.commit()
    return exploration_id, page_id, element_id, observation_id


def _scenario(page_id: UUID, element_id: UUID, observation_id: UUID, *, expected=True, confirmed=True, title="Select Details"):
    return {
        "title": title,
        "objective": "Check that the reviewed tab state is available.",
        "relevant_page_ids": [str(page_id)],
        "steps": [{
            "instruction": "",
            "evidence": [{"page_id": str(page_id), "element_ids": [str(element_id)]}],
            "interaction_observation": {
                "observation_id": str(observation_id),
                "expected_state": expected,
                "expected_state_confirmed": confirmed,
            },
        }],
        "expected_outcome": "The Details tab is selected.",
    }


def test_test_observation_endpoint_returns_only_current_observed_evidence(client):
    exploration_id, page_id, element_id, eligible_id = _seed_observation(client)
    with client.app.state.test_session_factory() as session:
        for marker, outcome in (("b", "blocked"), ("c", "skipped"), ("d", "failed")):
            session.add(
                InteractionObservation(
                    exploration_id=exploration_id,
                    page_id=page_id,
                    element_ordinal=0,
                    probe_key=marker * 64,
                    action_type="tab",
                    outcome=outcome,
                    reason=f"{outcome}_fixture",
                    before_evidence={},
                    after_evidence=None,
                    duration_ms=0,
                )
            )
        session.commit()
    response = client.get(f"/api/v1/explorations/{exploration_id}/test-observations")
    assert response.status_code == 200
    results = response.json()
    assert [item["id"] for item in results] == [str(eligible_id)]
    item = results[0]
    assert item["exploration_id"] == str(exploration_id)
    assert item["page_id"] == str(page_id)
    assert item["control"] == {
        "element_id": str(element_id),
        "element_ordinal": 0,
        "tag_name": "button",
        "role": "tab",
        "name": "Details",
    }
    assert item["before_state"]["selected"] is False
    assert item["observed_after_state"]["selected"] is True
    assert item["outcome"] == "observed"
    filtered = client.get(
        f"/api/v1/explorations/{exploration_id}/test-observations",
        params={"page_id": str(page_id)},
    )
    assert filtered.status_code == 200 and [row["id"] for row in filtered.json()] == [str(eligible_id)]
    assert client.get(
        f"/api/v1/explorations/{exploration_id}/test-observations",
        params={"page_id": str(uuid4())},
    ).status_code == 404


def test_case_and_run_snapshot_store_confirmed_observation_without_mutating_history(client):
    exploration_id, page_id, element_id, observation_id = _seed_observation(client)
    base = f"/api/v1/explorations/{exploration_id}"
    created = client.post(
        base + "/test-cases",
        json={
            "name": "Observed tab check",
            "description": "A confirmed tab transition.",
            "scenario": _scenario(page_id, element_id, observation_id),
        },
    )
    assert created.status_code == 201, created.text
    case = created.json()
    saved_step = case["definition"]["scenario"]["steps"][0]
    assert saved_step["interaction_observation"]["observation_id"] == str(observation_id)
    assert saved_step["interaction_observation"]["expected_state_confirmed"] is True
    assert saved_step["interaction_evidence_snapshot"]["observed_after_state"]["selected"] is True
    assert case["definition"]["steps"][0]["action"]["kind"] == "interaction_transition"

    run_response = client.post(base + f"/test-cases/{case['id']}/runs")
    assert run_response.status_code == 202, run_response.text
    run = run_response.json()
    saved_run_snapshot = run["scenario"]
    assert run["steps"][0]["action"]["observation_id"] == str(observation_id)
    assert run["steps"][0]["expected_evidence"]["expected_value"] is True

    update = client.patch(
        base + f"/test-cases/{case['id']}",
        json={"scenario": _scenario(page_id, element_id, observation_id, title="Revised Details check")},
    )
    assert update.status_code == 200, update.text
    detail = client.get(base + f"/test-runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["scenario"] == saved_run_snapshot
    assert detail.json()["scenario"]["scenario"]["title"] == "Select Details"

    with client.app.state.test_session_factory() as session:
        saved_observation = session.get(InteractionObservation, observation_id)
        saved_observation.outcome = "failed"
        saved_observation.reason = "evidence_changed"
        session.commit()
        case_row = session.get(TestCase, UUID(case["id"]))
        observations = load_interaction_observations(session, case_row.definition["scenario"])
        pages = list(session.scalars(select(ExplorationPage).where(ExplorationPage.exploration_id == exploration_id)))
        try:
            compile_test_case_definition(
                case_row.definition,
                pages,
                "https://fixture.example/",
                exploration_id=exploration_id,
                observations=observations,
                allow_observed_interactions=True,
            )
        except ValueError as exc:
            assert "observed" in str(exc).lower() or "eligible" in str(exc).lower()
        else:
            raise AssertionError("A failed observation must not remain executable.")


def test_disclosure_observation_compiles_aria_expanded_expectation(client):
    exploration_id, page_id, element_id, observation_id = _seed_observation(client, action_type="disclosure")
    available = client.get(f"/api/v1/explorations/{exploration_id}/test-observations")
    assert available.status_code == 200
    assert available.json()[0]["interaction_type"] == "disclosure"
    assert available.json()[0]["before_state"]["expanded"] is False
    assert available.json()[0]["observed_after_state"]["expanded"] is True

    scenario = _scenario(page_id, element_id, observation_id, title="Expand Help")
    scenario["objective"] = "Check that the reviewed disclosure state is available."
    scenario["expected_outcome"] = "The Help disclosure is expanded."
    created = client.post(
        f"/api/v1/explorations/{exploration_id}/test-cases",
        json={"name": "Disclosure state check", "scenario": scenario},
    )
    assert created.status_code == 201, created.text
    step = created.json()["definition"]["steps"][0]
    assert step["action"]["kind"] == "interaction_transition"
    assert step["action"]["interaction_type"] == "disclosure"
    assert step["action"]["attribute"] == "aria-expanded"
    assert step["action"]["expected_value"] is True


def test_observation_requires_matching_exploration_and_explicit_expected_state(client):
    first_exploration, page_id, element_id, observation_id = _seed_observation(client)
    other_exploration, other_page_id, other_element_id, _ = _seed_observation(client)

    wrong_exploration = client.post(
        f"/api/v1/explorations/{other_exploration}/test-cases",
        json={
            "name": "Wrong exploration reference",
            "scenario": _scenario(other_page_id, other_element_id, observation_id),
        },
    )
    assert wrong_exploration.status_code == 422

    unconfirmed = client.post(
        f"/api/v1/explorations/{first_exploration}/test-cases",
        json={
            "name": "Unconfirmed observation",
            "scenario": _scenario(page_id, element_id, observation_id, confirmed=False),
        },
    )
    assert unconfirmed.status_code == 422

    wrong_expected_state = client.post(
        f"/api/v1/explorations/{first_exploration}/test-cases",
        json={
            "name": "Wrong expected state",
            "scenario": _scenario(page_id, element_id, observation_id, expected=False),
        },
    )
    assert wrong_expected_state.status_code == 422

    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(TestCase.id).where(TestCase.exploration_id == first_exploration)) is None


def test_observation_step_rejects_missing_or_changed_control_evidence(client):
    exploration_id, page_id, element_id, observation_id = _seed_observation(client)
    with client.app.state.test_session_factory() as session:
        element = session.get(ObservedElement, element_id)
        element.accessible_name = "Different tab"
        session.commit()
    available = client.get(f"/api/v1/explorations/{exploration_id}/test-observations")
    assert available.status_code == 200 and available.json() == []

    response = client.post(
        f"/api/v1/explorations/{exploration_id}/test-cases",
        json={
            "name": "Stale observation evidence",
            "scenario": _scenario(page_id, element_id, observation_id),
        },
    )
    assert response.status_code == 422
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(TestCase.id).where(TestCase.exploration_id == exploration_id)) is None


def test_form_contained_observation_is_not_offered_or_saved(client):
    exploration_id, page_id, element_id, observation_id = _seed_observation(client, inside_form=True)
    available = client.get(f"/api/v1/explorations/{exploration_id}/test-observations")
    assert available.status_code == 200 and available.json() == []

    response = client.post(
        f"/api/v1/explorations/{exploration_id}/test-cases",
        json={
            "name": "Form interaction must stay unavailable",
            "scenario": _scenario(page_id, element_id, observation_id),
        },
    )
    assert response.status_code == 422
    with client.app.state.test_session_factory() as session:
        assert session.scalar(select(TestCase.id).where(TestCase.exploration_id == exploration_id)) is None


def test_deleted_observation_cannot_be_offered_or_saved(client):
    exploration_id, page_id, element_id, observation_id = _seed_observation(client)
    with client.app.state.test_session_factory() as session:
        session.delete(session.get(InteractionObservation, observation_id))
        session.commit()

    available = client.get(f"/api/v1/explorations/{exploration_id}/test-observations")
    assert available.status_code == 200 and available.json() == []
    response = client.post(
        f"/api/v1/explorations/{exploration_id}/test-cases",
        json={"name": "Deleted evidence", "scenario": _scenario(page_id, element_id, observation_id)},
    )
    assert response.status_code == 422
