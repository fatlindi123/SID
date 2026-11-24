from fastapi.testclient import TestClient

from app.core.url_security import UnsafeTargetUrl
from app.models.exploration import Exploration, ExplorationEvent, ExplorationPage, ObservedElement


def test_create_list_details_pages_and_events(
    client: TestClient, monkeypatch
) -> None:
    async def allow_public_url(value: str) -> str:
        return value

    monkeypatch.setattr("app.api.v1.explorations.assert_public_http_url", allow_public_url)
    response = client.post("/api/v1/explorations", json={"url": "https://docs.example"})

    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "queued"
    assert created["pages_discovered"] == 0
    assert created["event_count"] == 1
    assert created["max_pages"] == 20
    assert str(created["id"]) in [str(item) for item in client.app.state.exploration_manager.queued]

    exploration_id = created["id"]
    assert client.get("/api/v1/explorations").json()[0]["id"] == exploration_id
    assert client.get(f"/api/v1/explorations/{exploration_id}").json()["status"] == "queued"
    assert client.get(f"/api/v1/explorations/{exploration_id}/pages").json() == []
    events = client.get(f"/api/v1/explorations/{exploration_id}/events").json()
    assert [event["event_type"] for event in events] == ["exploration_queued"]


def test_saved_page_aggregation_returns_every_page_element_and_event_count(
    client: TestClient,
) -> None:
    session_factory = client.app.state.test_session_factory
    with session_factory() as session:
        exploration = Exploration(
            start_url="https://the-internet.herokuapp.com/",
            status="completed",
            max_pages=20,
            max_depth=3,
            pages_discovered=2,
            elements_discovered=19,
            event_count=3,
        )
        other_exploration = Exploration(
            start_url="https://example.org/",
            status="completed",
            max_pages=20,
            max_depth=3,
        )
        session.add_all([exploration, other_exploration])
        session.flush()

        pages = [
            ExplorationPage(
                exploration_id=exploration.id,
                url="https://the-internet.herokuapp.com/",
                title="The Internet",
                depth=0,
                http_status=200,
                element_count=18,
            ),
            ExplorationPage(
                exploration_id=exploration.id,
                url="https://the-internet.herokuapp.com/checkboxes",
                title="Checkboxes",
                depth=1,
                http_status=200,
                element_count=1,
            ),
            ExplorationPage(
                exploration_id=other_exploration.id,
                url="https://example.org/",
                title="Other exploration",
                depth=0,
                http_status=200,
                element_count=0,
            ),
        ]
        session.add_all(pages)
        session.flush()

        session.add_all(
            [
                ObservedElement(
                    page_id=pages[0].id,
                    ordinal=index,
                    kind="link",
                    tag_name="a",
                    role="link",
                    accessible_name=f"Link {index}",
                    selector=f'a:nth-of-type({index + 1})',
                    attributes={},
                )
                for index in range(18)
            ]
            + [
                ObservedElement(
                    page_id=pages[1].id,
                    ordinal=0,
                    kind="input",
                    tag_name="input",
                    role="checkbox",
                    accessible_name="Checkbox 1",
                    selector="input[type=checkbox]",
                    attributes={},
                )
            ]
            + [
                ExplorationEvent(
                    exploration_id=exploration.id,
                    sequence=sequence,
                    event_type="page_discovered",
                    message=f"Page {sequence} saved.",
                    details={},
                )
                for sequence in range(1, 4)
            ]
        )
        session.commit()
        exploration_id = exploration.id

    detail = client.get(f"/api/v1/explorations/{exploration_id}")
    assert detail.status_code == 200
    assert detail.json()["event_count"] == 3

    response = client.get(f"/api/v1/explorations/{exploration_id}/pages")
    assert response.status_code == 200
    result_pages = response.json()
    assert len(result_pages) == 2
    assert [page["url"] for page in result_pages] == [
        "https://the-internet.herokuapp.com/",
        "https://the-internet.herokuapp.com/checkboxes",
    ]
    assert [len(page["elements"]) for page in result_pages] == [18, 1]
    assert sum(len(page["elements"]) for page in result_pages) == 19
    assert result_pages[0]["elements"][-1]["selector"] == 'a:nth-of-type(18)'


def test_rejects_unsafe_urls_with_structured_error(
    client: TestClient, monkeypatch
) -> None:
    async def reject_private_url(value: str) -> str:
        raise UnsafeTargetUrl("Private and reserved IP addresses are not allowed.")

    monkeypatch.setattr("app.api.v1.explorations.assert_public_http_url", reject_private_url)
    response = client.post("/api/v1/explorations", json={"url": "http://127.0.0.1"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_target_url"


def test_cancel_marks_queued_exploration_cancelled(
    client: TestClient, monkeypatch
) -> None:
    async def allow_public_url(value: str) -> str:
        return value

    monkeypatch.setattr("app.api.v1.explorations.assert_public_http_url", allow_public_url)
    created = client.post("/api/v1/explorations", json={"url": "https://docs.example"}).json()

    response = client.post(f"/api/v1/explorations/{created['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    events = client.get(f"/api/v1/explorations/{created['id']}/events").json()
    assert events[-1]["event_type"] == "exploration_cancelled"


def test_unknown_exploration_returns_structured_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/explorations/4fa7f98f-8449-47e6-8f06-c4ee65f3618b")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "exploration_not_found"
