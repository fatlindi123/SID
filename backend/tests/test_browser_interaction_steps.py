import asyncio
import ipaddress
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import BACKEND_ROOT, Settings
from app.services.browser_explorer import BrowserExplorationEngine


def _response(body: str) -> bytes:
    html = f"<!doctype html><html><head><title>Interaction Fixture</title></head><body>{body}</body></html>".encode()
    return (
        b"HTTP/1.1 200 OK\r\n"
        + b"Content-Type: text/html; charset=utf-8\r\n"
        + f"Content-Length: {len(html)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + html
    )


async def _run_interaction_fixture(body: str, *, action_type: str, before: bool, expected: bool, timeout: float = 1.5):
    hits: list[tuple[str, str]] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode("latin-1").split()
            method, path = (line[0], line[1].split("?", 1)[0]) if len(line) >= 2 else ("GET", "/")
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            hits.append((method, path))
            writer.write(_response(body) if path == "/" else _response("<h1>Unexpected request</h1>"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    root_url = f"http://fixture.test:{port}/"

    async def allow_fixture(url: str) -> str:
        if not url.startswith(root_url):
            raise ValueError("Only the local browser fixture is allowed in this test.")
        return url

    async def resolve_fixture(host: str, _port: int):
        if host != "fixture.test":
            raise ValueError("Only the local browser fixture is allowed in this test.")
        return {ipaddress.ip_address("127.0.0.1")}

    settings = Settings(
        _env_file=None,
        playwright_browsers_path=Path(BACKEND_ROOT / ".cache" / "playwright"),
        exploration_page_timeout_seconds=timeout,
    )
    engine = BrowserExplorationEngine(settings, url_validator=allow_fixture, address_resolver=resolve_fixture)
    finished: list[tuple[str, str | None, dict | None]] = []

    async def on_started(_ordinal: int) -> None:
        return None

    async def on_finished(_ordinal: int, status: str, _duration: int, error: str | None, evidence: dict | None) -> None:
        finished.append((status, error, evidence))

    attribute = "aria-selected" if action_type == "tab" else "aria-expanded"
    role = "tab" if action_type == "tab" else "button"
    target_id = "details-tab" if action_type == "tab" else "help-disclosure"
    controls = "details-panel" if action_type == "tab" else "help-panel"
    name = "Details" if action_type == "tab" else "Help"
    action = {
        "kind": "interaction_transition",
        "observation_id": str(uuid4()),
        "page_id": "saved-page",
        "page_url": root_url,
        "page_title": "Interaction Fixture",
        "interaction_type": action_type,
        "element_id": str(uuid4()),
        "element_ordinal": 0,
        "tag_name": "button",
        "role": role,
        "name": name,
        "controls": controls,
        "attribute": attribute,
        "before_value": before,
        "expected_value": expected,
    }

    try:
        await engine.execute_test_plan(
            start_url=root_url,
            saved_pages=[{"id": "saved-page", "url": root_url, "title": "Interaction Fixture"}],
            steps=[{"action": action}],
            on_step_started=on_started,
            on_step_finished=on_finished,
            is_cancelled=lambda: False,
        )
        return finished, hits
    finally:
        server.close()
        await server.wait_closed()


def _control_html(action_type: str, *, transition: bool = True, unsafe_request: str | None = None) -> str:
    if action_type == "tab":
        control = '<div role="tablist"><button id="details-tab" type="button" role="tab" aria-selected="false" aria-controls="details-panel">Details</button></div><section id="details-panel" role="tabpanel">Details content</section>'
        attr = "aria-selected"
    else:
        control = '<button id="help-disclosure" type="button" aria-expanded="false" aria-controls="help-panel">Help</button><section id="help-panel">Help content</section>'
        attr = "aria-expanded"
    handler = ""
    if transition:
        handler += f"document.getElementById('{ 'details-tab' if action_type == 'tab' else 'help-disclosure' }').setAttribute('{attr}', 'true');"
    if unsafe_request == "post":
        handler += "fetch('/mutation', {method:'POST'}).catch(() => {});"
    elif unsafe_request == "cross-origin":
        handler += "fetch('http://outside.invalid/pixel').catch(() => {});"
    elif unsafe_request == "navigate":
        handler += "window.location.href='/about';"
    return control + f"<script>document.querySelector('[{attr}]').addEventListener('click', () => {{{handler}}});</script>"


@pytest.mark.parametrize("action_type", ["tab", "disclosure"])
def test_chromium_executes_confirmed_tab_and_disclosure_transitions(action_type):
    async def run():
        return await _run_interaction_fixture(
            _control_html(action_type), action_type=action_type, before=False, expected=True
        )

    finished, hits = asyncio.run(run())
    assert finished[0][0] == "passed"
    assert finished[0][1] is None
    assert finished[0][2]["changed"] is True
    assert finished[0][2]["actual_value"] is True
    assert hits == [("GET", "/")]


@pytest.mark.parametrize("unsafe_request", ["post", "cross-origin", "navigate"])
def test_chromium_fails_closed_when_interaction_requests_or_navigates(unsafe_request):
    async def run():
        return await _run_interaction_fixture(
            _control_html("tab", unsafe_request=unsafe_request),
            action_type="tab",
            before=False,
            expected=True,
        )

    finished, hits = asyncio.run(run())
    assert finished[0][0] == "failed"
    assert finished[0][1] and "request was blocked" in finished[0][1]
    assert all(method == "GET" and path == "/" for method, path in hits)


def test_chromium_never_activates_a_form_contained_control():
    async def run():
        return await _run_interaction_fixture(
            f"<form>{_control_html('disclosure')}</form>",
            action_type="disclosure",
            before=False,
            expected=True,
        )

    finished, hits = asyncio.run(run())
    assert finished[0][0] == "failed"
    assert "no longer eligible" in finished[0][1]
    assert hits == [("GET", "/")]


def test_chromium_fails_when_observed_state_does_not_change():
    async def run():
        return await _run_interaction_fixture(
            _control_html("disclosure", transition=False),
            action_type="disclosure",
            before=False,
            expected=True,
            timeout=1,
        )

    finished, _hits = asyncio.run(run())
    assert finished[0][0] == "failed"
    assert "did not reach its explicitly expected state" in finished[0][1]


def test_chromium_fails_before_click_when_saved_initial_state_is_stale():
    async def run():
        return await _run_interaction_fixture(
            _control_html("tab"),
            action_type="tab",
            before=True,
            expected=False,
        )

    finished, _hits = asyncio.run(run())
    assert finished[0][0] == "failed"
    assert "no longer matches the saved before-state" in finished[0][1]


def test_chromium_verifies_an_explicit_false_disclosure_expectation():
    body = _control_html("disclosure").replace(
        "aria-expanded=\"false\"", "aria-expanded=\"true\""
    ).replace(
        "setAttribute('aria-expanded', 'true')", "setAttribute('aria-expanded', 'false')"
    )

    async def run():
        return await _run_interaction_fixture(
            body,
            action_type="disclosure",
            before=True,
            expected=False,
        )

    finished, hits = asyncio.run(run())
    assert finished[0][0] == "passed"
    assert finished[0][2]["before_value"] is True
    assert finished[0][2]["expected_value"] is False
    assert hits == [("GET", "/")]
