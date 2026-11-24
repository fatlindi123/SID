import asyncio
import ipaddress
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.url_security import UnsafeTargetUrl
from app.db.base import Base
from app.models.exploration import Exploration, ExplorationEvent, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.services.browser_explorer import BrowserExplorationEngine
from app.services.exploration_worker import ExplorationManager
from app import models  # noqa: F401 - register metadata


class LocalSiteHandler(BaseHTTPRequestHandler):
    submitted_methods: list[str] = []
    dangerous_gets: list[str] = []
    reset_hits: list[str] = []
    navigation_hits: list[str] = []
    restore_hits: int = 0
    cancel_hits: int = 0
    cancel_event = threading.Event()
    cancel_callback = None

    def do_GET(self) -> None:
        if self.path == "/timeout":
            content = b"""<!doctype html><title>Timeout fixture</title>
            <button type="button" aria-expanded="false" aria-controls="panel">Toggle panel</button>
            <div id="panel" hidden>Panel</div>
            <div style="position:fixed;inset:0;background:#fff;z-index:99">Overlay</div>"""
            self.send_response(200)
        elif self.path == "/restore":
            type(self).restore_hits += 1
            title = "Changed baseline" if type(self).restore_hits >= 3 else "Restore fixture"
            content = f"""<!doctype html><html><head><title>{title}</title></head><body>
            <button type="button" aria-expanded="false" aria-controls="panel">More details</button>
            <div id="panel" hidden><h2>Panel</h2></div>
            <script>document.querySelector('button').addEventListener('click', (event) => {{
              const expanded = event.currentTarget.getAttribute('aria-expanded') !== 'true';
              event.currentTarget.setAttribute('aria-expanded', String(expanded));
              document.querySelector('#panel').hidden = !expanded;
            }});</script></body></html>""".encode()
            self.send_response(200)
        elif self.path == "/interrupt":
            type(self).cancel_hits += 1
            if type(self).cancel_hits >= 2:
                type(self).cancel_event.set()
                if type(self).cancel_callback:
                    type(self).cancel_callback()
            content = b"""<!doctype html><html><head><title>Cancel fixture</title></head><body>
            <button type="button" aria-expanded="false" aria-controls="panel">More details</button>
            <div id="panel" hidden><h2>Panel</h2></div></body></html>"""
            self.send_response(200)
        elif self.path.startswith("/danger/delete"):
            type(self).dangerous_gets.append(self.path)
            content = b"This must never be requested"
            self.send_response(200)
        elif self.path == "/reset-hit":
            type(self).reset_hits.append(self.path)
            content = b"Reset must never be activated"
            self.send_response(200)
        elif self.path == "/next-page":
            type(self).navigation_hits.append(self.path)
            content = b"The out-of-page navigation should be blocked"
            self.send_response(200)
        elif self.path == "/products":
            content = b"""<!doctype html><html><head><title>Products</title></head>
            <body><main><h1>Products</h1><a href="/">Home</a></main></body></html>"""
            self.send_response(200)
        elif self.path == "/":
            content = b"""<!doctype html><html><head><title>Demo storefront</title>
            <meta name="description" content="A deterministic local test site"></head>
            <body><nav aria-label="Primary"><a href="/products">Products</a>
            <a href="/products">Products again</a><a href="https://outside.example/">Outside</a>
            <a href="/danger/delete">Delete page</a></nav>
            <main><h1>Welcome</h1><form action="/submitted" method="post">
            <label for="search">Search products</label><input id="search" name="query" placeholder="Search">
            <label for="password">Password</label><input id="password" name="password" type="password" value="fixture-secret-value">
            <button type="submit" aria-label="Search now">Search</button>
            <button type="reset">Reset search</button></form>
            <form id="hidden-actions" action="/submitted" method="post" hidden></form>
            <button type="submit" form="hidden-actions">Linked submit</button>
            <button type="reset" onclick="fetch('/reset-hit')">Reset outside</button>
            <div role="tablist" aria-label="Product information">
              <button type="button" role="tab" aria-selected="true" aria-controls="overview-panel">Overview</button>
              <button type="button" role="tab" aria-selected="false" aria-controls="spec-panel">Specifications</button>
            </div>
            <section id="overview-panel"><h2>Overview panel</h2></section>
            <section id="spec-panel" hidden><h2>Specifications panel</h2></section>
            <button type="button" aria-expanded="false" aria-controls="details-panel">More details</button>
            <div id="details-panel" hidden><h2>Details panel</h2></div>
            <button type="button" aria-expanded="false" aria-controls="post-panel">Load extra details</button>
            <div id="post-panel" hidden><h2>Extra details</h2></div>
            <button type="button" aria-expanded="false" aria-controls="put-panel">Update extra details</button>
            <div id="put-panel" hidden><h2>Updated details</h2></div>
            <button type="button" aria-expanded="false" aria-controls="patch-panel">Change extra details</button>
            <div id="patch-panel" hidden><h2>Changed details</h2></div>
            <button type="button" aria-expanded="false" aria-controls="delete-method-panel">Open extra details</button>
            <div id="delete-method-panel" hidden><h2>Opened details</h2></div>
            <button type="button" aria-expanded="false" aria-controls="delete-panel">Delete account</button>
            <div id="delete-panel" hidden>Unsafe</div>
            <button type="button" aria-expanded="false" aria-controls="unsafe-get-panel">Load profile details</button>
            <div id="unsafe-get-panel" hidden>Private action</div>
            <button type="button" aria-expanded="false" aria-controls="navigation-panel">Open details page</button>
            <div id="navigation-panel" hidden>Navigation action</div>
            <script>
              const tabs = [...document.querySelectorAll('[role=tab]')];
              tabs.forEach((tab) => tab.addEventListener('click', () => {
                tabs.forEach((item) => {
                  const active = item === tab;
                  item.setAttribute('aria-selected', String(active));
                  document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
                });
              }));
              document.querySelector('[aria-controls=details-panel]').addEventListener('click', (event) => {
                const button = event.currentTarget;
                const expanded = button.getAttribute('aria-expanded') !== 'true';
                button.setAttribute('aria-expanded', String(expanded));
                document.getElementById('details-panel').hidden = !expanded;
              });
              document.querySelector('[aria-controls=post-panel]').addEventListener('click', (event) => {
                const button = event.currentTarget;
                button.setAttribute('aria-expanded', 'true');
                document.getElementById('post-panel').hidden = false;
                fetch('/transition', { method: 'POST' }).catch(() => {});
              });
              for (const [id, method] of [['put-panel', 'PUT'], ['patch-panel', 'PATCH'], ['delete-method-panel', 'DELETE']]) {
                document.querySelector('[aria-controls=' + id + ']').addEventListener('click', (event) => {
                  event.currentTarget.setAttribute('aria-expanded', 'true');
                  fetch('/transition', { method }).catch(() => {});
                });
              }
              document.querySelector('[aria-controls=delete-panel]').addEventListener('click', () => {
                fetch('/danger/delete', { method: 'GET' }).catch(() => {});
              });
              document.querySelector('[aria-controls=unsafe-get-panel]').addEventListener('click', () => {
                fetch('/danger/delete', { method: 'GET' }).catch(() => {});
              });
              document.querySelector('[aria-controls=navigation-panel]').addEventListener('click', () => {
                window.location.href = '/next-page';
              });
            </script></main></body></html>"""
            self.send_response(200)
        else:
            content = b"Not found"
            self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        type(self).submitted_methods.append(self.command)
        self.send_response(200)
        self.end_headers()

    def do_PUT(self) -> None:
        type(self).submitted_methods.append(self.command)
        self.send_response(200)
        self.end_headers()

    def do_PATCH(self) -> None:
        type(self).submitted_methods.append(self.command)
        self.send_response(200)
        self.end_headers()

    def do_DELETE(self) -> None:
        type(self).submitted_methods.append(self.command)
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_site() -> Iterator[str]:
    LocalSiteHandler.submitted_methods.clear()
    LocalSiteHandler.dangerous_gets.clear()
    LocalSiteHandler.reset_hits.clear()
    LocalSiteHandler.navigation_hits.clear()
    LocalSiteHandler.restore_hits = 0
    LocalSiteHandler.cancel_hits = 0
    LocalSiteHandler.cancel_event.clear()
    LocalSiteHandler.cancel_callback = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _local_url_validator(base_url: str):
    expected = urlsplit(base_url)

    async def validate(value: str) -> str:
        target = urlsplit(value)
        if target.scheme != "http" or target.hostname != expected.hostname or target.port != expected.port:
            raise UnsafeTargetUrl("Browser test can only reach its local fixture site.")
        return value

    return validate


def _local_address_resolver(base_url: str):
    expected = urlsplit(base_url)

    async def resolve(host: str, port: int):
        if host != expected.hostname or port != expected.port:
            raise UnsafeTargetUrl("Browser test can only connect to its local fixture site.")
        return {ipaddress.ip_address(host)}

    return resolve


def test_worker_persists_real_playwright_results_and_closes(
    local_site: str,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        exploration_max_pages=5,
        exploration_max_depth=2,
        exploration_page_timeout_seconds=10,
        exploration_worker_concurrency=1,
        interaction_max_probes_per_page=10,
        interaction_max_probes_per_exploration=10,
    )
    exploration_id = uuid4()
    with session_factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url=local_site,
            status="queued",
            max_pages=5,
            max_depth=2,
        )
        session.add(exploration)
        session.commit()

    manager = ExplorationManager(
        session_factory,
        settings,
        engine_factory=lambda: BrowserExplorationEngine(
            settings,
            _local_url_validator(local_site),
            _local_address_resolver(local_site),
        ),
    )

    async def run_to_completion() -> None:
        manager.enqueue(exploration_id)
        task = manager._tasks[exploration_id]
        await asyncio.wait_for(asyncio.shield(task), timeout=45)

    try:
        asyncio.run(run_to_completion())
        with session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            assert exploration is not None
            assert exploration.status == "completed", exploration.error_message
            pages = list(
                session.scalars(
                    select(ExplorationPage)
                    .options()
                    .where(ExplorationPage.exploration_id == exploration_id)
                    .order_by(ExplorationPage.url)
                )
            )
            assert len(pages) == 2
            assert len({page.url for page in pages}) == len(pages)
            assert {page.title for page in pages} == {"Demo storefront", "Products"}
            root_page = next(page for page in pages if page.title == "Demo storefront")
            assert root_page.screenshot and root_page.screenshot.startswith(b"\xff\xd8")
            assert root_page.dom_outline
            elements = list(session.scalars(select(ObservedElement).where(ObservedElement.page_id == root_page.id)))
            names = {element.accessible_name for element in elements}
            assert "Search now" in names
            assert "Search products" in names
            events = list(
                session.scalars(
                    select(ExplorationEvent)
                    .where(ExplorationEvent.exploration_id == exploration_id)
                    .order_by(ExplorationEvent.sequence)
                )
            )
            assert events[-1].event_type == "exploration_completed"
            assert exploration.pages_discovered == 2
            persisted_elements = list(
                session.scalars(
                    select(ObservedElement).where(
                        ObservedElement.page_id.in_([page.id for page in pages])
                    )
                )
            )
            assert exploration.elements_discovered == sum(page.element_count for page in pages)
            assert exploration.elements_discovered == len(persisted_elements)
            assert exploration.event_count == len(events)
            assert [event.sequence for event in events] == list(range(1, len(events) + 1))
            observations = list(
                session.scalars(
                    select(InteractionObservation).where(
                        InteractionObservation.exploration_id == exploration_id
                    )
                )
            )
            assert any(item.outcome == "observed" and item.action_type == "tab" for item in observations)
            assert any(item.outcome == "observed" and item.action_type == "disclosure" for item in observations)
            observed_tab = next(item for item in observations if item.outcome == "observed" and item.before_evidence.get("target", {}).get("name") == "Specifications")
            assert observed_tab.after_evidence["target"]["selected"] is True
            observed_disclosure = next(item for item in observations if item.outcome == "observed" and item.before_evidence.get("target", {}).get("name") == "More details")
            assert observed_disclosure.after_evidence["target"]["expanded"] is True
            blocked_method_controls = {
                item.before_evidence.get("target", {}).get("name")
                for item in observations
                if item.outcome == "blocked" and item.reason == "non_read_only_method"
            }
            assert blocked_method_controls >= {
                "Load extra details",
                "Update extra details",
                "Change extra details",
                "Open extra details",
            }
            assert any(
                item.outcome == "blocked"
                and item.reason == "unsafe_request_path"
                and item.before_evidence.get("target", {}).get("name") == "Load profile details"
                for item in observations
            )
            assert any(
                item.outcome == "blocked"
                and item.reason == "out_of_page_navigation"
                and item.before_evidence.get("target", {}).get("name") == "Open details page"
                for item in observations
            )
            assert any(item.outcome == "skipped" and item.reason == "inside_form" for item in observations)
            assert sum(item.outcome == "skipped" and item.reason == "inside_form" for item in observations) >= 3
            assert any(item.outcome == "skipped" and item.reason == "unsafe_or_missing_name" for item in observations)
            assert any(item.outcome == "skipped" and item.reason == "submit_or_reset" for item in observations)
            assert not any(item.before_evidence.get("target", {}).get("name") == "Products" for item in observations)
            serialized_evidence = json.dumps(
                [
                    (item.before_evidence, item.after_evidence)
                    for item in observations
                ]
            ).lower()
            assert "fixture-secret-value" not in serialized_evidence
            assert '"value"' not in serialized_evidence
        assert LocalSiteHandler.submitted_methods == []
        assert LocalSiteHandler.dangerous_gets == []
        assert LocalSiteHandler.reset_hits == []
        assert LocalSiteHandler.navigation_hits == []
    finally:
        asyncio.run(manager.stop())
        engine.dispose()


def test_probe_timeout_is_failed_and_not_successful(local_site: str) -> None:
    observations = asyncio.run(
        _run_direct_interaction(local_site.rstrip("/") + "/timeout", local_site, timeout=1)
    )
    assert observations
    assert observations[0]["outcome"] == "failed", observations[0]["reason"]
    assert observations[0]["reason"] == "interaction_timeout"


def test_restoration_mismatch_stops_later_probes(local_site: str) -> None:
    LocalSiteHandler.restore_hits = 0
    observations = asyncio.run(
        _run_direct_interaction(local_site.rstrip("/") + "/restore", local_site)
    )
    assert observations[0]["outcome"] == "failed"
    assert observations[0]["reason"] == "restoration_unverified"
    assert LocalSiteHandler.restore_hits == 3


def test_cancellation_before_click_is_persisted_as_failure(local_site: str) -> None:
    LocalSiteHandler.cancel_hits = 0
    LocalSiteHandler.cancel_event.clear()
    observations = asyncio.run(
        _run_direct_interaction(
            local_site.rstrip("/") + "/interrupt",
            local_site,
            cancel_event=LocalSiteHandler.cancel_event,
        )
    )
    assert LocalSiteHandler.cancel_hits >= 2
    assert observations[0]["outcome"] == "failed", observations[0]["reason"]
    assert observations[0]["reason"] == "cancelled_before_interaction"


def test_worker_persists_interrupted_probe_as_failed_before_cancellation(local_site: str) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        exploration_max_pages=1,
        exploration_max_depth=0,
        exploration_worker_concurrency=1,
    )
    exploration_id = uuid4()
    with session_factory() as session:
        session.add(
            Exploration(
                id=exploration_id,
                start_url=local_site.rstrip("/") + "/interrupt",
                status="queued",
                max_pages=1,
                max_depth=0,
            )
        )
        session.commit()

    manager = ExplorationManager(
        session_factory,
        settings,
        engine_factory=lambda: BrowserExplorationEngine(
            settings,
            _local_url_validator(local_site),
            _local_address_resolver(local_site),
        ),
    )

    def interrupt_worker() -> None:
        cancel_event = manager._cancel_events.get(exploration_id)
        if cancel_event is not None:
            cancel_event.set()

    LocalSiteHandler.cancel_hits = 0
    LocalSiteHandler.cancel_event.clear()
    LocalSiteHandler.cancel_callback = interrupt_worker

    async def run_worker() -> None:
        manager.enqueue(exploration_id)
        await asyncio.wait_for(asyncio.shield(manager._tasks[exploration_id]), timeout=30)

    try:
        asyncio.run(run_worker())
        with session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            observations = list(
                session.scalars(
                    select(InteractionObservation).where(
                        InteractionObservation.exploration_id == exploration_id
                    )
                )
            )
            assert exploration is not None and exploration.status == "cancelled"
            assert len(observations) == 1
            assert observations[0].outcome == "failed"
            assert observations[0].reason == "cancelled_before_interaction"
        assert LocalSiteHandler.submitted_methods == []
    finally:
        LocalSiteHandler.cancel_callback = None
        asyncio.run(manager.stop())
        engine.dispose()


def test_page_and_exploration_probe_limits_are_enforced(local_site: str) -> None:
    per_page = asyncio.run(
        _run_direct_interaction(
            local_site,
            local_site,
            max_probes_per_page=1,
            max_probes_per_exploration=10,
        )
    )
    assert sum(item["outcome"] != "skipped" for item in per_page) == 1
    assert any(item["reason"] == "probe_limit" for item in per_page)

    per_exploration = asyncio.run(
        _run_direct_interaction(
            local_site,
            local_site,
            max_probes_per_page=10,
            max_probes_per_exploration=1,
        )
    )
    assert sum(item["outcome"] != "skipped" for item in per_exploration) == 1
    assert any(item["reason"] == "probe_limit" for item in per_exploration)


async def _run_direct_interaction(
    target_url: str,
    base_url: str,
    timeout: int = 8,
    cancel_event: threading.Event | None = None,
    max_probes_per_page: int = 5,
    max_probes_per_exploration: int = 30,
) -> list[dict]:
    observations: list[dict] = []
    settings = Settings(
        _env_file=None,
        playwright_browsers_path=Settings(_env_file=None).playwright_browsers_path,
        exploration_max_pages=1,
        exploration_max_depth=0,
        interaction_probe_timeout_seconds=timeout,
        interaction_max_probes_per_page=max_probes_per_page,
        interaction_max_probes_per_exploration=max_probes_per_exploration,
    )

    async def on_page(page) -> None:
        page.persisted_id = uuid4()

    async def on_event(*_args) -> None:
        return None

    async def on_interaction(_page_id, observation) -> None:
        observations.append(observation)

    engine = BrowserExplorationEngine(
        settings,
        _local_url_validator(base_url),
        _local_address_resolver(base_url),
    )
    try:
        await engine.explore(
            target_url,
            on_page,
            on_event,
            lambda: bool(cancel_event and cancel_event.is_set()),
            on_interaction,
        )
    except asyncio.CancelledError:
        if cancel_event is None:
            raise
    return observations


def test_worker_cancellation_persists_cancelled_state() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = Settings(_env_file=None, database_url="sqlite+pysqlite:///:memory:")
    exploration_id = uuid4()
    with session_factory() as session:
        session.add(
            Exploration(
                id=exploration_id,
                start_url="https://public.example",
                status="queued",
                max_pages=20,
                max_depth=3,
            )
        )
        session.commit()
    started = asyncio.Event()

    class SlowEngine:
        async def explore(self, start_url, on_page, on_event, is_cancelled, on_interaction=None):
            started.set()
            while not is_cancelled():
                await asyncio.sleep(0.01)
            raise asyncio.CancelledError

    manager = ExplorationManager(session_factory, settings, engine_factory=SlowEngine)

    async def cancel_job() -> None:
        manager.enqueue(exploration_id)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await manager.cancel(exploration_id)

    try:
        asyncio.run(cancel_job())
        with session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            assert exploration is not None
            assert exploration.status == "cancelled"
            events = list(
                session.scalars(
                    select(ExplorationEvent).where(
                        ExplorationEvent.exploration_id == exploration_id
                    )
                )
            )
            assert events[-1].event_type == "exploration_cancelled"
    finally:
        engine.dispose()
