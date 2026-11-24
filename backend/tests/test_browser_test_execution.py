import asyncio
import ipaddress
from pathlib import Path

import pytest

from app.core.config import BACKEND_ROOT, Settings
from app.services.browser_explorer import BrowserExplorationEngine


def _response(title: str, body: str) -> bytes:
    html = f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>".encode()
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        + f"Content-Length: {len(html)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + html
    )


async def _run_fixture_case(source_body: str, routes: dict, target_path: str, target_title: str, selector: str = "a#catalog", link_name: str = "catalog"):
    hits: list[str] = []
    fixture_routes = {**routes, "/": _response("Fixture Home", source_body)}

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            parts = request_line.decode("latin-1").split()
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"
            while await reader.readline() not in {b"\r\n", b"\n", b""}:
                pass
            hits.append(path)
            response = fixture_routes.get(path)
            if response is None:
                response = _response("Not found", "missing")
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    root_url = f"http://fixture.test:{port}/"
    target_url = f"http://fixture.test:{port}{target_path}"

    async def allow_fixture(url: str) -> str:
        if not url.startswith(f"http://fixture.test:{port}/"):
            raise ValueError("Only the local browser fixture is allowed in this test.")
        return url

    async def resolve_fixture(host: str, _port: int):
        if host != "fixture.test":
            raise ValueError("Only the local browser fixture is allowed in this test.")
        return {ipaddress.ip_address("127.0.0.1")}

    settings = Settings(
        _env_file=None,
        playwright_browsers_path=Path(BACKEND_ROOT / ".cache" / "playwright"),
        exploration_page_timeout_seconds=6,
    )
    engine = BrowserExplorationEngine(settings, url_validator=allow_fixture, address_resolver=resolve_fixture)
    finished: list[tuple[str, str | None, dict | None]] = []

    async def on_started(_ordinal: int) -> None:
        return None

    async def on_finished(_ordinal: int, status: str, _duration_ms: int, error: str | None, evidence: dict | None) -> None:
        finished.append((status, error, evidence))

    pages = [
        {"id": "root", "url": root_url, "title": "Fixture Home"},
        {"id": "target", "url": target_url, "title": target_title},
    ]
    if "/products" in fixture_routes:
        pages.append({"id": "redirect-target", "url": f"http://fixture.test:{port}/products", "title": "Products"})

    action = {
        "kind": "click_link",
        "page_id": "root",
        "page_url": root_url,
        "target_page_id": "target",
        "target_url": target_url,
        "target_title": target_title,
        "element_id": "link-id",
        "selector": selector,
        "link_name": link_name,
    }

    try:
        await engine.execute_test_plan(
            start_url=root_url,
            saved_pages=pages,
            steps=[{"action": action}],
            on_step_started=on_started,
            on_step_finished=on_finished,
            is_cancelled=lambda: False,
        )
        return finished, hits
    finally:
        server.close()
        await server.wait_closed()


def test_existing_playwright_engine_allows_a_verified_static_internal_link() -> None:
    async def run():
        return await _run_fixture_case(
            '<a id="catalog" href="/catalog">Catalog</a>',
            {"/catalog": _response("Catalog", "<main>Products</main>")},
            "/catalog",
            "Catalog",
        )

    finished, hits = asyncio.run(run())

    assert finished[0][0] == "passed"
    assert finished[0][1] is None
    assert finished[0][2]["clicked"] is True
    assert finished[0][2]["url"].endswith("/catalog")
    assert hits.count("/catalog") == 1


@pytest.mark.parametrize(
    ("source_html", "routes", "target_path", "target_title", "selector", "link_name", "blocked_path"),
    [
        (
            '<a id="catalog" href="/delete-account">Delete account</a>',
            {"/delete-account": _response("Deleted", "changed")},
            "/delete-account",
            "Deleted",
            "a#catalog",
            "delete account",
            "/delete-account",
        ),
        (
            '<a href="/catalog">Catalog</a><a href="/catalog">Catalog</a>',
            {"/catalog": _response("Catalog", "Products")},
            "/catalog",
            "Catalog",
            "a",
            "catalog",
            "/catalog",
        ),
        (
            '<a id="catalog" href="/catalog">Catalog</a>',
            {
                "/catalog": b"HTTP/1.1 302 Found\r\nLocation: /products\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                "/products": _response("Products", "Redirect destination"),
            },
            "/catalog",
            "Catalog",
            "a#catalog",
            "catalog",
            "/products",
        ),
    ],
    ids=["destructive-label", "ambiguous-selector", "redirect"],
)
def test_chromium_blocks_ineligible_internal_links_and_redirects(
    source_html, routes, target_path, target_title, selector, link_name, blocked_path
) -> None:
    async def run():
        return await _run_fixture_case(
            source_html,
            routes,
            target_path,
            target_title,
            selector=selector,
            link_name=link_name,
        )

    finished, hits = asyncio.run(run())

    assert finished[0][0] == "failed"
    assert finished[0][1]
    assert blocked_path not in hits
