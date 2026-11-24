from uuid import uuid4

from fastapi.testclient import TestClient

from app.models.exploration import Exploration, ExplorationPage
from app.models.interaction_observation import InteractionObservation


def _saved_interaction(client: TestClient):
    exploration_id = uuid4()
    page_id = uuid4()
    with client.app.state.test_session_factory() as session:
        session.add(
            Exploration(
                id=exploration_id,
                start_url="https://fixture.example/",
                status="completed",
                max_pages=1,
                max_depth=0,
            )
        )
        session.add(
            ExplorationPage(
                id=page_id,
                exploration_id=exploration_id,
                url="https://fixture.example/",
                title="Fixture",
                depth=0,
                element_count=1,
            )
        )
        session.add(
            InteractionObservation(
                exploration_id=exploration_id,
                page_id=page_id,
                element_ordinal=0,
                probe_key="a" * 64,
                action_type="tab",
                outcome="observed",
                before_evidence={"url": "https://fixture.example/", "target": {"role": "tab", "name": "Details"}},
                after_evidence={"visible_changes": {"state_changes": [{"before": {"selected": False}, "after": {"selected": True}}]}},
                duration_ms=35,
            )
        )
        session.commit()
    return exploration_id, page_id


def test_interaction_observations_are_read_only_and_page_filter_is_scoped(client: TestClient) -> None:
    exploration_id, page_id = _saved_interaction(client)
    response = client.get(f"/api/v1/explorations/{exploration_id}/interactions")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["outcome"] == "observed"
    assert body[0]["before_evidence"]["target"]["name"] == "Details"

    filtered = client.get(
        f"/api/v1/explorations/{exploration_id}/interactions",
        params={"page_id": str(page_id)},
    )
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    other_page = client.get(
        f"/api/v1/explorations/{exploration_id}/interactions",
        params={"page_id": str(uuid4())},
    )
    assert other_page.status_code == 404


def test_unknown_exploration_has_consistent_not_found_response(client: TestClient) -> None:
    response = client.get(f"/api/v1/explorations/{uuid4()}/interactions")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "exploration_not_found"
