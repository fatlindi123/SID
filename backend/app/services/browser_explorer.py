"""Playwright-based, read-only website discovery."""

import asyncio
import logging
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.core.config import BACKEND_ROOT, Settings, get_settings
from app.core.test_navigation import is_safe_navigation_target, normalized_navigation_label
from app.core.url_security import (
    UnsafeTargetUrl,
    assert_public_http_url,
    normalized_http_url,
    resolve_public_addresses,
    same_origin,
)
from app.services.safe_egress_proxy import AddressResolver, SafeEgressProxy
from app.services.interaction_discovery import (
    INTERACTION_STATE_SCRIPT,
    InteractionPersistenceError,
    InteractionProbeRunner,
    ObservationCallback,
)

logger = logging.getLogger("software_intelligence.browser")

MAX_VISIBLE_TEXT_CHARS = 20000
MAX_ELEMENTS_PER_PAGE = 2000
MAX_ELEMENT_TEXT_CHARS = 2000
MAX_DOM_OUTLINE_ITEMS = 300
UNSAFE_NAVIGATION_SEGMENT = re.compile(
    r"(^|[-_/])(delete|remove|destroy|logout|signout|purchase|checkout|payment|subscribe|unsubscribe|cancel|confirm|submit)([-_/?#]|$)",
    re.IGNORECASE,
)
UNSAFE_TEST_NAVIGATION_SEGMENT = re.compile(
    r"(^|[-_/?&=])(delet\w*|remov\w*|destroy\w*|logout|signout|purchas\w*|checkout|payment|"
    r"subscrib\w*|unsubscrib\w*|cancel\w*|confirm\w*|submi\w*)([-_/?#&=]|$)",
    re.IGNORECASE,
)


@dataclass
class ExtractedElement:
    ordinal: int
    kind: str
    tag_name: str
    role: str | None
    accessible_name: str | None
    label: str | None
    text: str | None
    selector: str | None
    attributes: dict[str, Any]
    persisted_id: UUID | None = None


@dataclass
class ExtractedPage:
    url: str
    title: str
    description: str | None
    depth: int
    http_status: int | None
    visible_text: str
    dom_outline: list[dict[str, Any]]
    screenshot: bytes | None
    elements: list[ExtractedElement]
    links: list[str]
    interaction_baseline: dict[str, Any] | None = None
    persisted_id: UUID | None = None


PageCallback = Callable[[ExtractedPage], Awaitable[None]]
EventCallback = Callable[[str, str, str | None, dict[str, Any]], Awaitable[None]]
UrlValidator = Callable[[str], Awaitable[str]]
TestStepStarted = Callable[[int], Awaitable[None]]
TestStepFinished = Callable[[int, str, int, str | None, dict[str, Any] | None], Awaitable[None]]


_EXTRACT_PAGE_SCRIPT = r"""
() => {
  const clean = (value, limit = 1000) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return text ? text.slice(0, limit) : null;
  };
  const isVisible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const roleFor = (element) => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit.split(/\s+/)[0];
    const tag = element.tagName.toLowerCase();
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'form') return 'form';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'aside') return 'complementary';
    if (tag === 'select') return element.multiple ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (element.getAttribute('type') || 'text').toLowerCase();
      if (type === 'hidden') return null;
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'img') return 'img';
    return null;
  };
  const labelFor = (element) => {
    const labels = element.labels ? Array.from(element.labels).map((item) => item.innerText) : [];
    if (labels.length) return clean(labels.join(' '));
    const labelledBy = element.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' ');
      if (text.trim()) return clean(text);
    }
    return clean(element.getAttribute('aria-label') || element.getAttribute('title'));
  };
  const accessibleNameFor = (element, label) => {
    const tag = element.tagName.toLowerCase();
    const type = (element.getAttribute('type') || '').toLowerCase();
    const imageAlt = tag === 'img' ? element.getAttribute('alt') : null;
    return clean(
      element.getAttribute('aria-label') || label ||
      (element.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map((id) => document.getElementById(id)?.innerText || '').join(' ') ||
      imageAlt || element.getAttribute('placeholder') ||
      ((tag === 'input' && ['button', 'submit', 'reset'].includes(type)) ? element.getAttribute('value') : '') ||
      element.innerText || element.textContent
    );
  };
  const selectorFor = (element) => {
    const tag = element.tagName.toLowerCase();
    if (element.id) return tag + '#' + CSS.escape(element.id);
    const testId = element.getAttribute('data-testid');
    if (testId) return tag + '[data-testid="' + testId.replace(/"/g, '\\"') + '"]';
    const role = element.getAttribute('role');
    return role ? tag + '[role="' + role + '"]' : tag;
  };
  const candidates = Array.from(document.querySelectorAll(
    'button,a[href],form,input,select,textarea,nav,main,aside,[role],[tabindex],[onclick]'
  ));
  const seen = new Set();
  const elements = [];
  for (const element of candidates) {
    if (seen.has(element) || !isVisible(element)) continue;
    seen.add(element);
    const tag = element.tagName.toLowerCase();
    const role = roleFor(element);
    if (tag === 'input' && (element.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
    const label = labelFor(element);
    const attributes = {};
    for (const name of ['id', 'type', 'name', 'placeholder', 'title', 'role', 'aria-label', 'data-testid', 'aria-expanded', 'aria-selected', 'aria-controls', 'aria-disabled']) {
      const value = element.getAttribute(name);
      if (value !== null) attributes[name] = value.slice(0, 500);
    }
    attributes.inside_form = !!(element.closest('form') || element.form);
    if (role === 'tab') attributes.inside_tablist = !!element.closest('[role="tablist"]');
    if (element.hasAttribute('aria-controls')) {
      const ids = (element.getAttribute('aria-controls') || '').trim().split(/\s+/).filter(Boolean);
      attributes.controls_valid = ids.length > 0 && ids.length <= 5 && ids.every((id) => !!document.getElementById(id));
    }
    if (tag === 'a' && element.href) attributes.href = element.href.slice(0, 2048);
    if (element.hasAttribute('disabled')) attributes.disabled = true;
    if (element.hasAttribute('required')) attributes.required = true;
    const kind = tag === 'a' ? 'link' :
      tag === 'form' ? 'form' :
      tag === 'nav' ? 'navigation' :
      ['input', 'select', 'textarea'].includes(tag) ? 'input' :
      role === 'button' || tag === 'button' ? 'button' :
      role === 'main' || role === 'complementary' ? 'landmark' :
      'control';
    elements.push({
      kind,
      tag_name: tag,
      role,
      accessible_name: accessibleNameFor(element, label),
      label,
      text: clean(element.innerText || element.textContent, 2000),
      selector: selectorFor(element),
      attributes
    });
    if (elements.length >= 2000) break;
  }
  const outline = Array.from(document.querySelectorAll(
    'header,nav,main,aside,footer,section,article,h1,h2,h3,h4,h5,h6'
  )).filter(isVisible).slice(0, 300).map((element) => {
    const tag = element.tagName.toLowerCase();
    const label = clean(element.getAttribute('aria-label') || element.getAttribute('aria-labelledby')
      ?.split(/\s+/).map((id) => document.getElementById(id)?.innerText || '').join(' '));
    return {
      tag_name: tag,
      role: roleFor(element),
      heading_level: /^h[1-6]$/.test(tag) ? Number(tag.slice(1)) : null,
      name: clean(element.innerText || element.textContent, 300),
      label
    };
  });
  return {
    title: clean(document.title, 1000) || '',
    description: clean(document.querySelector('meta[name="description"]')?.content, 4000),
    visible_text: clean(document.body?.innerText || '', 20000) || '',
    elements,
    dom_outline: outline
  };
}
"""

_SAFETY_INIT_SCRIPT = r"""
(() => {
  document.addEventListener('submit', (event) => event.preventDefault(), true);
  const blockSubmit = () => { throw new DOMException('Form submission is disabled during exploration.', 'SecurityError'); };
  HTMLFormElement.prototype.submit = blockSubmit;
  HTMLFormElement.prototype.requestSubmit = blockSubmit;
  window.open = () => null;
})();
"""


class RootPageError(RuntimeError):
    """The initial target could not be loaded or inspected."""


class BrowserAssertionError(RuntimeError):
    """A real browser assertion failed with a safe, user-facing message."""


class BrowserExplorationEngine:
    def __init__(
        self,
        settings: Settings | None = None,
        url_validator: UrlValidator = assert_public_http_url,
        address_resolver: AddressResolver | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.url_validator = url_validator
        self.address_resolver = address_resolver or resolve_public_addresses
        browser_cache = self.settings.playwright_browsers_path
        if not browser_cache.is_absolute():
            browser_cache = BACKEND_ROOT / browser_cache
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)

    async def explore(
        self,
        start_url: str,
        on_page: PageCallback,
        on_event: EventCallback,
        is_cancelled: Callable[[], bool],
        on_interaction: ObservationCallback | None = None,
    ) -> None:
        normalized_start = normalized_http_url(start_url)
        start_origin = normalized_start
        pages_seen: set[str] = set()
        pending: deque[tuple[str, int]] = deque([(normalized_start, 0)])
        root_loaded = False
        blocked_requests: list[str] = []
        interactions_probed = 0
        interactions_saved = 0

        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=True)
            proxy = SafeEgressProxy(self.address_resolver)
            try:
                await proxy.start()
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    service_workers="block",
                    proxy={"server": proxy.server_url, "bypass": "<-loopback>"},
                )
                await context.add_init_script(_SAFETY_INIT_SCRIPT)

                async def guard_request(route: Any) -> None:
                    request = route.request
                    requested_url = request.url
                    try:
                        if is_cancelled():
                            raise UnsafeTargetUrl("The exploration is being cancelled.")
                        await self.url_validator(requested_url)
                        if request.method not in {"GET", "HEAD"}:
                            raise UnsafeTargetUrl("Only read-only GET and HEAD requests are allowed.")
                        if request.is_navigation_request() and not same_origin(start_origin, requested_url):
                            raise UnsafeTargetUrl("Cross-origin page navigation is not allowed.")
                        if request.is_navigation_request() and normalized_http_url(requested_url) != normalized_start:
                            path = request.url.split("?", 1)[0]
                            if UNSAFE_NAVIGATION_SEGMENT.search(path):
                                raise UnsafeTargetUrl("Potentially destructive page navigation was blocked.")
                    except (UnsafeTargetUrl, ValueError):
                        blocked_requests.append(requested_url[:2048])
                        await route.abort("blockedbyclient")
                        return
                    except Exception:
                        blocked_requests.append(requested_url[:2048])
                        await route.abort("blockedbyclient")
                        return
                    await route.continue_()

                await context.route("**/*", guard_request)

                while pending and len(pages_seen) < self.settings.exploration_max_pages:
                    if is_cancelled():
                        raise asyncio.CancelledError
                    target_url, depth = pending.popleft()
                    target_url = normalized_http_url(target_url)
                    if target_url in pages_seen:
                        continue
                    pages_seen.add(target_url)
                    await on_event("navigation_started", "Opening page for inspection.", target_url, {"depth": depth})
                    page = await context.new_page()
                    response_status: int | None = None
                    try:
                        try:
                            response = await page.goto(
                                target_url,
                                wait_until="domcontentloaded",
                                timeout=int(self.settings.exploration_page_timeout_seconds * 1000),
                            )
                            response_status = response.status if response else None
                        except PlaywrightTimeoutError:
                            if is_cancelled():
                                raise asyncio.CancelledError
                            await on_event("page_timeout", "The page load reached its time limit; inspecting available content.", target_url, {"timeout_seconds": self.settings.exploration_page_timeout_seconds})
                        except PlaywrightError as exc:
                            if is_cancelled():
                                raise asyncio.CancelledError
                            if not root_loaded:
                                raise RootPageError("The target website could not be loaded.") from exc
                            await on_event("page_failed", "The page could not be loaded.", target_url, {"reason": type(exc).__name__})
                            continue

                        if is_cancelled():
                            raise asyncio.CancelledError
                        final_url = normalized_http_url(page.url) if page.url.startswith(("http://", "https://")) else target_url
                        if not same_origin(start_origin, final_url):
                            await on_event("navigation_blocked", "A cross-origin redirect was blocked.", target_url, {})
                            if not root_loaded:
                                raise RootPageError("The target redirected to a different origin.")
                            continue
                        snapshot = await page.evaluate(_EXTRACT_PAGE_SCRIPT)
                        if is_cancelled():
                            raise asyncio.CancelledError
                        try:
                            screenshot = await page.screenshot(
                                type="jpeg", quality=60, full_page=False, timeout=10000
                            )
                        except PlaywrightError:
                            screenshot = None

                        elements = [
                            ExtractedElement(
                                ordinal=index,
                                kind=element["kind"],
                                tag_name=element["tag_name"],
                                role=element["role"],
                                accessible_name=element["accessible_name"],
                                label=element["label"],
                                text=element["text"],
                                selector=element["selector"],
                                attributes=element["attributes"],
                            )
                            for index, element in enumerate(snapshot["elements"])
                        ]
                        controlled_ids = list(dict.fromkeys(
                            control_id
                            for element in elements
                            for control_id in str(element.attributes.get("aria-controls", "")).split()
                            if control_id
                        ))[:100]
                        interaction_baseline = await page.evaluate(
                            INTERACTION_STATE_SCRIPT, {"targetIds": controlled_ids}
                        )
                        extracted = ExtractedPage(
                            url=final_url,
                            title=snapshot["title"],
                            description=snapshot["description"],
                            depth=depth,
                            http_status=response_status,
                            visible_text=snapshot["visible_text"][:MAX_VISIBLE_TEXT_CHARS],
                            dom_outline=snapshot["dom_outline"][:MAX_DOM_OUTLINE_ITEMS],
                            screenshot=screenshot,
                            elements=elements[:MAX_ELEMENTS_PER_PAGE],
                            links=[
                                element.attributes["href"]
                                for element in elements
                                if element.kind == "link" and element.attributes.get("href")
                            ],
                            interaction_baseline=interaction_baseline,
                        )
                        await on_page(extracted)
                        root_loaded = True
                        await on_event(
                            "page_discovered",
                            "Page metadata and visible interface elements were saved.",
                            final_url,
                            {
                                "depth": depth,
                                "http_status": response_status,
                                "element_count": len(extracted.elements),
                            },
                        )

                        if (
                            on_interaction is not None
                            and extracted.persisted_id is not None
                            and interactions_saved < self.settings.interaction_max_observations_per_exploration
                        ):
                            runner = InteractionProbeRunner(
                                self.settings,
                                self.url_validator,
                                self.address_resolver,
                            )
                            used_probes, saved_observations = await runner.run_page(
                                browser=browser,
                                page=extracted,
                                page_id=extracted.persisted_id,
                                on_observation=on_interaction,
                                is_cancelled=is_cancelled,
                                remaining_probe_budget=max(
                                    0,
                                    self.settings.interaction_max_probes_per_exploration
                                    - interactions_probed,
                                ),
                                remaining_observation_budget=max(
                                    0,
                                    self.settings.interaction_max_observations_per_exploration
                                    - interactions_saved,
                                ),
                            )
                            interactions_probed += used_probes
                            interactions_saved += saved_observations
                            if is_cancelled():
                                raise asyncio.CancelledError

                        if depth < self.settings.exploration_max_depth:
                            for link in extracted.links:
                                try:
                                    candidate = normalized_http_url(urljoin(final_url, link))
                                except (UnsafeTargetUrl, ValueError):
                                    continue
                                if not same_origin(start_origin, candidate) or candidate in pages_seen:
                                    continue
                                if UNSAFE_NAVIGATION_SEGMENT.search(candidate.split("?", 1)[0]):
                                    continue
                                if len(pages_seen) + len(pending) >= self.settings.exploration_max_pages:
                                    break
                                pending.append((candidate, depth + 1))

                        if blocked_requests:
                            unique_blocked = list(dict.fromkeys(blocked_requests))
                            await on_event(
                                "requests_blocked",
                                "Unsafe or non-read-only browser requests were blocked.",
                                final_url,
                                {"count": len(unique_blocked), "sample_urls": unique_blocked[:10]},
                            )
                            blocked_requests.clear()
                    except RootPageError:
                        raise
                    except asyncio.CancelledError:
                        raise
                    except InteractionPersistenceError:
                        raise
                    except Exception as exc:
                        if not root_loaded:
                            raise RootPageError("The target website could not be inspected.") from exc
                        logger.exception("page_inspection_failed", extra={"url": target_url})
                        await on_event("page_failed", "The page could not be inspected.", target_url, {"reason": type(exc).__name__})
                    finally:
                        await page.close()

                if not root_loaded:
                    raise RootPageError("The target website did not return an inspectable page.")
            finally:
                await browser.close()
                await proxy.close()

    async def execute_test_plan(
        self,
        start_url: str,
        saved_pages: list[dict[str, Any]],
        steps: list[dict[str, Any]],
        on_step_started: TestStepStarted,
        on_step_finished: TestStepFinished,
        is_cancelled: Callable[[], bool],
    ) -> None:
        """Run the validated action subset in the same isolated Playwright runtime."""
        normalized_start = normalized_http_url(start_url)
        pages_by_url: dict[str, dict[str, Any]] = {}
        for saved_page in saved_pages:
            saved_url = normalized_http_url(saved_page["url"])
            if not same_origin(normalized_start, saved_url):
                raise BrowserAssertionError("A saved execution page is outside the explored site origin.")
            pages_by_url[saved_url] = saved_page

        timeout_ms = int(self.settings.exploration_page_timeout_seconds * 1000)
        browser_cache = self.settings.playwright_browsers_path
        if not browser_cache.is_absolute():
            browser_cache = BACKEND_ROOT / browser_cache
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_cache)

        async with async_playwright() as playwright:
            browser: Browser = await playwright.chromium.launch(headless=True)
            proxy = SafeEgressProxy(self.address_resolver)
            try:
                await proxy.start()
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    service_workers="block",
                    proxy={"server": proxy.server_url, "bypass": "<-loopback>"},
                )
                context.set_default_timeout(timeout_ms)
                context.set_default_navigation_timeout(timeout_ms)
                await context.add_init_script(_SAFETY_INIT_SCRIPT)
                interaction_page_url: str | None = None
                interaction_blocked: list[str] = []

                async def guard_request(route: Any) -> None:
                    request = route.request
                    requested_url = request.url
                    try:
                        if is_cancelled():
                            raise UnsafeTargetUrl("The test run is being cancelled.")
                        await self.url_validator(requested_url)
                        if request.method not in {"GET", "HEAD"}:
                            if interaction_page_url is not None:
                                interaction_blocked.append("non_read_only_request")
                            raise UnsafeTargetUrl("Only read-only GET and HEAD requests are allowed.")
                        if interaction_page_url is not None:
                            if not same_origin(interaction_page_url, requested_url):
                                interaction_blocked.append("cross_origin_request")
                                raise UnsafeTargetUrl("Observed interactions cannot contact another origin.")
                            if UNSAFE_TEST_NAVIGATION_SEGMENT.search(requested_url):
                                interaction_blocked.append("unsafe_interaction_request")
                                raise UnsafeTargetUrl("A potentially state-changing interaction request was blocked.")
                        if request.is_navigation_request():
                            normalized_requested = normalized_http_url(requested_url)
                            if interaction_page_url is not None:
                                interaction_blocked.append("interaction_navigation")
                                raise UnsafeTargetUrl("Observed interactions cannot start another page navigation.")
                            if not same_origin(normalized_start, requested_url):
                                raise UnsafeTargetUrl("Cross-origin page navigation is not allowed.")
                            if normalized_requested not in pages_by_url:
                                raise UnsafeTargetUrl("Navigation is limited to pages saved by this exploration.")
                            if request.redirected_from is not None:
                                raise UnsafeTargetUrl("Redirected page navigation is not allowed during test execution.")
                            if not is_safe_navigation_target(requested_url):
                                raise UnsafeTargetUrl("Navigation is not an explicitly supported static content route.")
                            if UNSAFE_TEST_NAVIGATION_SEGMENT.search(requested_url):
                                raise UnsafeTargetUrl("Potentially destructive page navigation was blocked.")
                    except (UnsafeTargetUrl, ValueError):
                        if interaction_page_url is not None:
                            interaction_blocked.append("request_policy_blocked")
                        await route.abort("blockedbyclient")
                        return
                    except Exception:
                        if interaction_page_url is not None:
                            interaction_blocked.append("request_policy_error")
                        await route.abort("blockedbyclient")
                        return
                    await route.continue_()

                await context.route("**/*", guard_request)
                page = await context.new_page()
                # BrowserContext routing only intercepts the first request in a
                # redirect chain. Use Chromium's response interception to stop
                # document redirects before navigation reaches an unverified URL.
                cdp_session = await context.new_cdp_session(page)

                async def guard_redirect_response(event: dict[str, Any]) -> None:
                    request_id = event.get("requestId")
                    if not isinstance(request_id, str):
                        return
                    status = event.get("responseStatusCode")
                    try:
                        if (
                            event.get("resourceType") == "Document"
                            and isinstance(status, int)
                            and 300 <= status < 400
                        ):
                            if interaction_page_url is not None:
                                interaction_blocked.append("interaction_redirect")
                            await cdp_session.send(
                                "Fetch.failRequest",
                                {"requestId": request_id, "errorReason": "BlockedByClient"},
                            )
                        else:
                            await cdp_session.send("Fetch.continueResponse", {"requestId": request_id})
                    except Exception:
                        # A response must never remain paused when inspection
                        # fails; fail closed instead of letting it continue.
                        try:
                            await cdp_session.send(
                                "Fetch.failRequest",
                                {"requestId": request_id, "errorReason": "BlockedByClient"},
                            )
                        except Exception:
                            pass

                cdp_session.on(
                    "Fetch.requestPaused",
                    lambda event: asyncio.create_task(guard_redirect_response(event)),
                )
                await cdp_session.send(
                    "Fetch.enable",
                    {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]},
                )

                async def open_saved_page(url: str) -> dict[str, Any]:
                    expected = pages_by_url.get(normalized_http_url(url))
                    if expected is None:
                        raise BrowserAssertionError("The requested page is not part of this saved exploration.")
                    response = await page.goto(
                        expected["url"],
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    actual_url = normalized_http_url(page.url)
                    actual_title = await page.title()
                    if actual_url != normalized_http_url(expected["url"]):
                        raise BrowserAssertionError("The browser did not reach the expected saved page.")
                    if actual_title != expected["title"]:
                        raise BrowserAssertionError("The loaded page title did not match the saved page title.")
                    return {
                        "page_id": expected["id"],
                        "url": actual_url,
                        "title": actual_title,
                        "http_status": response.status if response else None,
                    }

                for ordinal, step in enumerate(steps):
                    if is_cancelled():
                        raise asyncio.CancelledError
                    await on_step_started(ordinal)
                    started = time.perf_counter()
                    status_value = "passed"
                    error_message: str | None = None
                    evidence: dict[str, Any] | None = None
                    try:
                        action = step["action"]
                        kind = action.get("kind")
                        if kind == "navigate":
                            evidence = await open_saved_page(action["url"])
                        elif kind == "assert_visible":
                            page_url = normalized_http_url(action["page_url"])
                            if page.url.startswith(("http://", "https://")) and normalized_http_url(page.url) == page_url:
                                actual_page = pages_by_url[page_url]
                                page_evidence = {
                                    "page_id": actual_page["id"],
                                    "url": page_url,
                                    "title": await page.title(),
                                    "http_status": None,
                                }
                            else:
                                page_evidence = await open_saved_page(page_url)
                            locator = page.locator(action["selector"])
                            match_count = await locator.count()
                            if match_count != 1:
                                raise BrowserAssertionError(
                                    f"The saved locator matched {match_count} elements; exactly one was expected."
                                )
                            visible = await locator.is_visible()
                            evidence = {
                                **page_evidence,
                                "element_id": action["element_id"],
                                "selector": action["selector"],
                                "visible": visible,
                            }
                            if not visible:
                                raise BrowserAssertionError("The saved interface element is not visible.")
                        elif kind == "interaction_transition":
                            page_url = normalized_http_url(action["page_url"])
                            if page_url not in pages_by_url or not is_safe_navigation_target(page_url):
                                raise BrowserAssertionError("The observed interaction page is not an approved saved page.")
                            if not page.url.startswith(("http://", "https://")) or normalized_http_url(page.url) != page_url:
                                await open_saved_page(page_url)
                            if page.url != page_url or await page.title() != action["page_title"]:
                                raise BrowserAssertionError("The current page no longer matches the saved interaction evidence.")

                            interaction_type = action.get("interaction_type")
                            role = action.get("role")
                            tag_name = action.get("tag_name")
                            name = action.get("name")
                            attribute = action.get("attribute")
                            if (
                                interaction_type not in {"tab", "disclosure"}
                                or role != ("tab" if interaction_type == "tab" else "button")
                                or tag_name not in {"button", "div"}
                                or attribute != ("aria-selected" if interaction_type == "tab" else "aria-expanded")
                                or not isinstance(name, str)
                                or not name
                            ):
                                raise BrowserAssertionError("The saved interaction type or control semantics are unsupported.")

                            locator = page.get_by_role(role, name=name, exact=True)
                            match_count = await locator.count()
                            if match_count != 1:
                                raise BrowserAssertionError("The saved interaction control is missing or ambiguous.")
                            tag_matches = await locator.locator(f"xpath=self::{tag_name}").count() == 1
                            explicit_role = await locator.get_attribute("role")
                            inside_form = (
                                await locator.locator("xpath=ancestor::form").count() > 0
                                or await locator.get_attribute("form") is not None
                            )
                            control_type = (await locator.get_attribute("type") or "").casefold()
                            aria_disabled = (await locator.get_attribute("aria-disabled") or "").casefold() == "true"
                            live_controls = await locator.get_attribute("aria-controls")
                            if (
                                not tag_matches
                                or inside_form
                                or aria_disabled
                                or (tag_name == "button" and control_type != "button")
                                or (interaction_type == "tab" and explicit_role != "tab")
                                or (interaction_type == "disclosure" and explicit_role not in {None, "button"})
                                or (tag_name == "div" and explicit_role != role)
                                or live_controls != action.get("controls")
                                or not await locator.is_visible()
                                or not await locator.is_enabled()
                            ):
                                raise BrowserAssertionError("The saved interaction control is no longer eligible.")

                            before_value = action.get("before_value")
                            expected_value = action.get("expected_value")
                            actual_before = await locator.get_attribute(attribute)
                            if (
                                not isinstance(before_value, bool)
                                or not isinstance(expected_value, bool)
                                or before_value == expected_value
                                or actual_before != str(before_value).lower()
                            ):
                                raise BrowserAssertionError("The live control state no longer matches the saved before-state.")

                            interaction_blocked.clear()
                            interaction_page_url = page_url
                            try:
                                # Do not let Playwright wait for a navigation that
                                # our request guard is intentionally blocking.
                                await locator.click(timeout=timeout_ms, no_wait_after=True)
                                await page.wait_for_timeout(50)
                                if interaction_blocked:
                                    raise BrowserAssertionError(
                                        "A non-read-only, cross-origin, or unapproved request was blocked during this interaction."
                                    )
                                deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
                                actual_after = await locator.get_attribute(attribute)
                                while actual_after != str(expected_value).lower():
                                    if is_cancelled():
                                        raise asyncio.CancelledError
                                    if asyncio.get_running_loop().time() >= deadline:
                                        raise BrowserAssertionError("The observed control did not reach its explicitly expected state.")
                                    await asyncio.sleep(0.05)
                                    actual_after = await locator.get_attribute(attribute)
                                await page.wait_for_timeout(50)
                            finally:
                                interaction_page_url = None
                            if interaction_blocked:
                                raise BrowserAssertionError("A non-read-only, cross-origin, or unapproved request was blocked during this interaction.")
                            if page.url != page_url or await page.title() != action["page_title"]:
                                raise BrowserAssertionError("The observed interaction attempted to leave its saved page.")
                            evidence = {
                                "observation_id": action["observation_id"],
                                "page_id": action["page_id"],
                                "url": page_url,
                                "title": await page.title(),
                                "interaction_type": interaction_type,
                                "element_id": action["element_id"],
                                "control": {"role": role, "name": name, "tag_name": tag_name},
                                "attribute": attribute,
                                "before_value": before_value,
                                "expected_value": expected_value,
                                "actual_value": expected_value,
                                "changed": True,
                            }
                        elif kind == "click_link":
                            source_url = normalized_http_url(action["page_url"])
                            if not page.url.startswith(("http://", "https://")) or normalized_http_url(page.url) != source_url:
                                await open_saved_page(source_url)
                            locator = page.locator(action["selector"])
                            match_count = await locator.count()
                            if match_count != 1:
                                raise BrowserAssertionError(
                                    f"The saved link locator matched {match_count} elements; exactly one was expected."
                                )
                            live_link = await locator.evaluate(
                                """(element) => {
                                  if (!(element instanceof HTMLAnchorElement)) return null;
                                  let current = element;
                                  while (current) {
                                    if (current.hasAttribute('onclick')) return null;
                                    current = current.parentElement;
                                  }
                                  const imageNames = Array.from(element.querySelectorAll('img'))
                                    .map((image) => image.getAttribute('alt') || '')
                                    .filter(Boolean);
                                  const directName = element.getAttribute('aria-label') ||
                                    element.innerText || imageNames.join(' ') || '';
                                  return {
                                    href: element.href,
                                    name: directName.replace(/\\s+/g, ' ').trim(),
                                    target: element.getAttribute('target') || '',
                                    hasDownload: element.hasAttribute('download'),
                                    insideForm: Boolean(element.closest('form')),
                                    disabled: element.getAttribute('aria-disabled') === 'true',
                                    role: element.getAttribute('role') || 'link'
                                  };
                                }"""
                            )
                            if not isinstance(live_link, dict):
                                raise BrowserAssertionError("The saved link is no longer an eligible internal navigation link.")
                            live_name = normalized_navigation_label(live_link.get("name"))
                            live_target_url = live_link.get("href")
                            if (
                                live_name != action.get("link_name")
                                or live_target_url != action.get("target_url")
                                or live_link.get("target", "").casefold() not in {"", "_self"}
                                or live_link.get("hasDownload")
                                or live_link.get("insideForm")
                                or live_link.get("disabled")
                                or live_link.get("role", "").casefold() != "link"
                                or not isinstance(live_target_url, str)
                                or not is_safe_navigation_target(live_target_url)
                            ):
                                raise BrowserAssertionError("The live link is ambiguous or outside the safe navigation policy.")
                            href = await locator.get_attribute("href")
                            actual_target = normalized_http_url(urljoin(page.url, href)) if href else ""
                            if actual_target != action["target_url"]:
                                raise BrowserAssertionError("The saved link destination changed since exploration.")
                            await locator.click(timeout=timeout_ms)
                            current_url = normalized_http_url(page.url)
                            if current_url != action["target_url"]:
                                raise BrowserAssertionError("Clicking the saved link did not reach its saved destination.")
                            actual_title = await page.title()
                            if actual_title != action["target_title"]:
                                raise BrowserAssertionError("The link destination title did not match the saved page title.")
                            target_page = pages_by_url[current_url]
                            evidence = {
                                "page_id": target_page["id"],
                                "url": current_url,
                                "title": actual_title,
                                "element_id": action["element_id"],
                                "selector": action["selector"],
                                "clicked": True,
                            }
                        else:
                            raise BrowserAssertionError("The saved step uses an unsupported browser action.")
                    except asyncio.CancelledError:
                        raise
                    except PlaywrightTimeoutError:
                        status_value = "failed"
                        error_message = "The browser test step timed out while waiting for the saved page or element."
                    except BrowserAssertionError as exc:
                        status_value = "failed"
                        error_message = str(exc)[:500]
                    except UnsafeTargetUrl:
                        status_value = "failed"
                        error_message = "The browser blocked a request that did not meet the saved-page security policy."
                    except PlaywrightError as exc:
                        status_value = "failed"
                        error_message = f"The browser action failed ({type(exc).__name__})."
                    except (KeyError, TypeError, ValueError):
                        status_value = "failed"
                        error_message = "The saved execution step is malformed or no longer valid."

                    if is_cancelled():
                        raise asyncio.CancelledError
                    duration_ms = max(0, int((time.perf_counter() - started) * 1000))
                    await on_step_finished(ordinal, status_value, duration_ms, error_message, evidence)
                    if status_value == "failed":
                        break
            finally:
                await browser.close()
                await proxy.close()
