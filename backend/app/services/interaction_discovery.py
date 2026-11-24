"""Bounded, read-only probes for explicitly marked tabs and disclosures."""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError

from app.core.config import Settings
from app.core.url_security import (
    UnsafeTargetUrl,
    normalized_http_url,
    same_origin,
)
from app.services.safe_egress_proxy import AddressResolver

logger = logging.getLogger("software_intelligence.interactions")


class InteractionPersistenceError(RuntimeError):
    """An observation could not be durably saved."""


class _ProbeCancelled(Exception):
    pass

ObservationOutcome = Literal["observed", "skipped", "blocked", "failed"]
ActionType = Literal["tab", "disclosure", "unknown"]
ObservationCallback = Callable[[UUID, dict[str, Any]], Awaitable[None]]

MAX_VISIBLE_EVIDENCE = 40
MAX_VISIBLE_CHANGES = 12
MAX_CONTROLLED_IDS = 5
MAX_LABEL_CHARS = 120
UNSAFE_LABEL = re.compile(
    r"\b(delete|remove|destroy|logout|sign\s*out|sign\s*in|log\s*in|register|"
    r"password|credential|checkout|purchase|payment|pay|buy|subscribe|unsubscribe|"
    r"confirm|submit|reset|cancel|save|send|upload|transfer)\b",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})|(?:\b\+?\d[\d ().-]{7,}\d\b)|"
    r"(?:\b(?:api[_ -]?key|token|secret|password)\s*[:=])",
    re.IGNORECASE,
)
UNSAFE_REQUEST_PATH = re.compile(
    r"(^|[-_/])(delete|remove|destroy|logout|signout|purchase|checkout|payment|subscribe|unsubscribe|cancel|confirm|submit)([-_/?#]|$)",
    re.IGNORECASE,
)
SENSITIVE_PATH = re.compile(r"(^|/)(auth|token|secret|password|credential|session)(/|$)", re.IGNORECASE)


INTERACTION_STATE_SCRIPT = r"""
({targetIds}) => {
  const clean = (value) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return text ? text.slice(0, 240) : null;
  };
  const visible = (element) => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const roleFor = (element) => {
    const explicit = element.getAttribute('role');
    if (explicit) return explicit.split(/\s+/)[0];
    const tag = element.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'nav') return 'navigation';
    if (tag === 'main') return 'main';
    if (tag === 'aside') return 'complementary';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'summary') return 'button';
    return null;
  };
  const nodes = new Set(document.querySelectorAll(
    'button,[role],nav,main,aside,summary,details,h1,h2,h3,h4,[aria-hidden],[aria-expanded],[aria-selected]'
  ));
  for (const id of targetIds) {
    const target = document.getElementById(id);
    if (target) nodes.add(target);
  }
  const elements = [];
  for (const element of nodes) {
    if (elements.length >= 100 || !visible(element) || element.closest('form') || element.form) continue;
    const tag = element.tagName.toLowerCase();
    if (['input', 'select', 'textarea', 'option', 'a'].includes(tag)) continue;
    const role = roleFor(element);
    let name = element.getAttribute('aria-label') || element.getAttribute('title');
    if (!name && /^h[1-6]$/.test(tag)) name = element.innerText || element.textContent;
    if (!name && ['button', 'summary'].includes(tag)) name = element.innerText || element.textContent;
    if (!name && role) {
      const labelledBy = element.getAttribute('aria-labelledby');
      if (labelledBy) name = labelledBy.split(/\s+/)
        .map((id) => document.getElementById(id)?.innerText || '').join(' ');
    }
    const expanded = element.getAttribute('aria-expanded');
    const selected = element.getAttribute('aria-selected');
    elements.push({
      tag_name: tag,
      role,
      name: clean(name),
      expanded: expanded === 'true' ? true : expanded === 'false' ? false : null,
      selected: selected === 'true' ? true : selected === 'false' ? false : null,
    });
  }
  return { url: window.location.href, title: clean(document.title), elements };
}
"""


@dataclass
class ProbeCandidate:
    ordinal: int
    action_type: ActionType
    reason: str | None
    tag_name: str
    role: str | None
    name: str | None
    expanded: bool | None
    selected: bool | None
    attributes: dict[str, Any]
    probe_key: str


@dataclass
class ProbeResult:
    observation: dict[str, Any]
    restored: bool
    cancelled: bool = False


def _safe_text(value: Any, limit: int = MAX_LABEL_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    if not cleaned or len(cleaned) > limit or SENSITIVE_TEXT.search(cleaned):
        return None
    return cleaned


def _safe_url(value: str) -> str:
    """Drop query and fragment data so tokens in URLs are never persisted."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        path = parsed.path[:500]
        if SENSITIVE_TEXT.search(path) or SENSITIVE_PATH.search(path):
            path = "/[redacted]"
        return urlunsplit((parsed.scheme, host, path, "", ""))
    except ValueError:
        return ""


def _controlled_ids(elements: list[Any]) -> list[str]:
    ids: list[str] = []
    for element in elements:
        raw = element.attributes.get("aria-controls")
        if not isinstance(raw, str):
            continue
        for item in raw.split():
            if 0 < len(item) <= 100 and item not in ids:
                ids.append(item)
            if len(ids) >= 100:
                return ids
    return ids


def _probe_key(element: Any, action_type: str) -> str:
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
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()


def _candidate_reason(element: Any) -> tuple[ActionType, str | None, str | None]:
    tag = str(element.tag_name or "").lower()
    role = str(element.role or "").lower() or None
    attributes = element.attributes or {}
    # Links and link-like controls are never considered for activation.
    if tag == "a" or role == "link":
        return "unknown", "link_not_supported", None

    inside_form = bool(attributes.get("inside_form"))
    name = _safe_text(element.accessible_name)
    control_type = str(attributes.get("type", "")).lower()
    if inside_form:
        return "unknown", "inside_form", None
    if tag in {"input", "select", "textarea"} or control_type in {"submit", "reset"}:
        return "unknown", "submit_or_reset", None
    if attributes.get("disabled") or str(attributes.get("aria-disabled", "")).lower() == "true":
        return "unknown", "disabled", name
    if name is None or UNSAFE_LABEL.search(name):
        return "unknown", "unsafe_or_missing_name", None

    expanded = str(attributes.get("aria-expanded", "")).lower()
    selected = str(attributes.get("aria-selected", "")).lower()
    is_tab = role == "tab"
    if is_tab:
        if tag not in {"button", "div"}:
            return "tab", "ambiguous_control", name
        if tag == "button" and control_type != "button":
            return "tab", "submit_or_reset", name
        if not attributes.get("inside_tablist") or selected not in {"true", "false"}:
            return "tab", "ambiguous_control", name
        return "tab", None, name

    if expanded in {"true", "false"}:
        if tag not in {"button", "div"} or role != "button":
            return "disclosure", "ambiguous_control", name
        if tag == "button" and control_type != "button":
            return "disclosure", "submit_or_reset", name
        if not attributes.get("controls_valid"):
            return "disclosure", "ambiguous_control", name
        return "disclosure", None, name

    if tag == "button" or role == "button":
        return "unknown", "unsupported_control", name
    return "unknown", "unsupported_control", name


def build_candidates(elements: list[Any], max_candidates: int) -> list[ProbeCandidate]:
    candidates: list[ProbeCandidate] = []
    for element in elements:
        tag = str(element.tag_name or "").lower()
        role = str(element.role or "").lower()
        attrs = element.attributes or {}
        # The bounded queue includes native buttons (so submit/reset/form
        # exclusions are auditable) and explicit tab/disclosure semantics.
        if tag != "button" and role not in {"button", "tab"} and "aria-expanded" not in attrs:
            continue
        action, reason, name = _candidate_reason(element)
        candidates.append(
            ProbeCandidate(
                ordinal=int(element.ordinal),
                action_type=action,
                reason=reason,
                tag_name=tag[:40],
                role=role or None,
                name=name,
                expanded=(attrs.get("aria-expanded") == "true") if attrs.get("aria-expanded") in {"true", "false"} else None,
                selected=(attrs.get("aria-selected") == "true") if attrs.get("aria-selected") in {"true", "false"} else None,
                attributes=dict(attrs),
                probe_key=_probe_key(element, action),
            )
        )
        if len(candidates) >= max_candidates:
            break
    return candidates


def _sanitize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in snapshot.get("elements", []):
        if not isinstance(raw, dict):
            continue
        item = {
            "tag_name": str(raw.get("tag_name", ""))[:40],
            "role": _safe_text(raw.get("role"), 80),
            "name": _safe_text(raw.get("name")),
            "expanded": raw.get("expanded") if isinstance(raw.get("expanded"), bool) else None,
            "selected": raw.get("selected") if isinstance(raw.get("selected"), bool) else None,
        }
        identity = (item["tag_name"], item["role"], item["name"])
        if identity in seen:
            # Preserve duplicate controls as a count-free set would hide
            # ambiguity; the actual selector lookup still requires one match.
            elements.append(item)
        else:
            seen.add(identity)
            elements.append(item)
        if len(elements) >= MAX_VISIBLE_EVIDENCE:
            break
    return {
        "url": _safe_url(str(snapshot.get("url", ""))),
        "title": _safe_text(snapshot.get("title"), 160),
        "visible_elements": elements,
    }


def _fingerprint(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (item.get("tag_name"), item.get("role"), item.get("name"))

    before_map = {key(item): item for item in before["visible_elements"]}
    after_map = {key(item): item for item in after["visible_elements"]}
    added = [after_map[item] for item in after_map.keys() - before_map.keys()]
    removed = [before_map[item] for item in before_map.keys() - after_map.keys()]
    changed = [
        {"before": before_map[item], "after": after_map[item]}
        for item in before_map.keys() & after_map.keys()
        if before_map[item] != after_map[item]
    ]
    sort_key = lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True)
    return {
        "added": sorted(added, key=sort_key)[:MAX_VISIBLE_CHANGES],
        "removed": sorted(removed, key=sort_key)[:MAX_VISIBLE_CHANGES],
        "state_changes": sorted(changed, key=sort_key)[:MAX_VISIBLE_CHANGES],
    }


def _target_evidence(candidate: ProbeCandidate, inside_form: bool = False) -> dict[str, Any]:
    return {
        "tag_name": candidate.tag_name,
        "role": candidate.role,
        "name": None if inside_form else candidate.name,
        "expanded": candidate.expanded,
        "selected": candidate.selected,
    }


def _evidence(
    snapshot: dict[str, Any],
    candidate: ProbeCandidate,
    visible_changes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe = _sanitize_snapshot(snapshot)
    target_state = next(
        (
            {
                "tag_name": candidate.tag_name,
                "role": _safe_text(item.get("role"), 80),
                "name": _safe_text(item.get("name")),
                "expanded": item.get("expanded") if isinstance(item.get("expanded"), bool) else None,
                "selected": item.get("selected") if isinstance(item.get("selected"), bool) else None,
            }
            for item in snapshot.get("elements", [])
            if isinstance(item, dict)
            and str(item.get("tag_name", "")).lower() == candidate.tag_name
            and str(item.get("role", "")).lower() == candidate.role
            and _safe_text(item.get("name")) == candidate.name
        ),
        _target_evidence(candidate),
    )
    return {
        **safe,
        "target": target_state,
        "visible_changes": visible_changes or {"added": [], "removed": [], "state_changes": []},
    }


def _base_record(
    page_id: UUID,
    candidate: ProbeCandidate,
    outcome: ObservationOutcome,
    reason: str | None,
    before: dict[str, Any],
    after: dict[str, Any] | None,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "element_ordinal": candidate.ordinal,
        "probe_key": candidate.probe_key,
        "action_type": candidate.action_type,
        "outcome": outcome,
        "reason": reason,
        "before_evidence": before,
        "after_evidence": after,
        "duration_ms": max(0, min(int(duration_ms), 120_000)),
    }


class InteractionProbeRunner:
    def __init__(
        self,
        settings: Settings,
        url_validator: Callable[[str], Awaitable[str]],
        address_resolver: AddressResolver,
    ) -> None:
        self.settings = settings
        self.url_validator = url_validator
        self.address_resolver = address_resolver

    async def run_page(
        self,
        browser: Browser,
        page: Any,
        page_id: UUID,
        on_observation: ObservationCallback,
        is_cancelled: Callable[[], bool],
        remaining_probe_budget: int,
        remaining_observation_budget: int,
    ) -> tuple[int, int]:
        candidates = build_candidates(
            page.elements, self.settings.interaction_max_candidates_per_page
        )
        if not candidates or remaining_observation_budget <= 0:
            return 0, 0

        target_ids = _controlled_ids(page.elements)
        baseline = _sanitize_snapshot(
            page.interaction_baseline
            or {"url": page.url, "title": page.title, "elements": []}
        )
        probe_count = 0
        observation_count = 0
        stop_page_reason: str | None = None

        for candidate in candidates:
            if is_cancelled():
                break
            if observation_count >= remaining_observation_budget:
                break

            if stop_page_reason:
                record = _base_record(
                    page_id,
                    candidate,
                    "skipped",
                    stop_page_reason,
                    {
                        "url": _safe_url(page.url),
                        "title": _safe_text(page.title, 160),
                        "target": _target_evidence(candidate, bool(candidate.attributes.get("inside_form"))),
                    },
                    None,
                    0,
                )
                await on_observation(page_id, record)
                observation_count += 1
                continue

            if candidate.reason:
                record = _base_record(
                    page_id,
                    candidate,
                    "skipped",
                    candidate.reason,
                    {
                        "url": _safe_url(page.url),
                        "title": _safe_text(page.title, 160),
                        "target": _target_evidence(candidate, bool(candidate.attributes.get("inside_form"))),
                    },
                    None,
                    0,
                )
                await on_observation(page_id, record)
                observation_count += 1
                continue

            if probe_count >= self.settings.interaction_max_probes_per_page or probe_count >= remaining_probe_budget:
                record = _base_record(
                    page_id,
                    candidate,
                    "skipped",
                    "probe_limit",
                    {
                        "url": _safe_url(page.url),
                        "title": _safe_text(page.title, 160),
                        "target": _target_evidence(candidate),
                    },
                    None,
                    0,
                )
                await on_observation(page_id, record)
                observation_count += 1
                continue

            result = await self._probe(
                browser=browser,
                page_url=page.url,
                candidate=candidate,
                target_ids=target_ids,
                baseline=baseline,
                page_id=page_id,
                is_cancelled=is_cancelled,
            )
            probe_count += 1
            await on_observation(page_id, result.observation)
            observation_count += 1
            if not result.restored:
                stop_page_reason = "restoration_unverified"
            if result.cancelled or is_cancelled():
                break

        return probe_count, observation_count

    async def _new_context(self, browser: Browser, proxy_server: str) -> BrowserContext:
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            service_workers="block",
            proxy={"server": proxy_server, "bypass": "<-loopback>"},
        )
        await context.add_init_script(
            """(() => {
              document.addEventListener('submit', (event) => event.preventDefault(), true);
              const blockSubmit = () => { throw new DOMException('Form submission is disabled.', 'SecurityError'); };
              HTMLFormElement.prototype.submit = blockSubmit;
              HTMLFormElement.prototype.requestSubmit = blockSubmit;
              window.open = () => null;
            })();"""
        )
        return context

    async def _probe(
        self,
        browser: Browser,
        page_url: str,
        candidate: ProbeCandidate,
        target_ids: list[str],
        baseline: dict[str, Any],
        page_id: UUID,
        is_cancelled: Callable[[], bool],
    ) -> ProbeResult:
        started = time.monotonic()
        blocked_reasons: list[str] = []
        after_evidence: dict[str, Any] | None = None
        probe_context: BrowserContext | None = None
        restored = False
        cancelled = False
        outcome: ObservationOutcome = "failed"
        reason = "probe_failed"
        before_evidence = {
            "url": _safe_url(page_url),
            "title": _safe_text(baseline.get("title"), 160),
            "target": _target_evidence(candidate),
            "visible_elements": baseline.get("visible_elements", [])[:MAX_VISIBLE_EVIDENCE],
            "visible_changes": {"added": [], "removed": [], "state_changes": []},
        }
        after_snapshot: dict[str, Any] | None = None
        timeout_ms = int(self.settings.interaction_probe_timeout_seconds * 1000)

        async def guard(route: Any) -> None:
            request = route.request
            try:
                if is_cancelled():
                    raise UnsafeTargetUrl("cancelled")
                await self.url_validator(request.url)
                if request.method not in {"GET", "HEAD"}:
                    raise UnsafeTargetUrl("non_read_only_method")
                parsed = urlsplit(request.url)
                if UNSAFE_REQUEST_PATH.search(parsed.path):
                    raise UnsafeTargetUrl("unsafe_request_path")
                if request.is_navigation_request() and (
                    not same_origin(page_url, request.url)
                    or normalized_http_url(request.url) != normalized_http_url(page_url)
                ):
                    raise UnsafeTargetUrl("out_of_page_navigation")
            except UnsafeTargetUrl as exc:
                blocked_reasons.append(str(exc)[:80])
                await route.abort("blockedbyclient")
                return
            except Exception:
                blocked_reasons.append("request_policy_error")
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        try:
            if is_cancelled():
                cancelled = True
                reason = "cancelled_before_probe"
                return ProbeResult(
                    self._record(page_id, candidate, "failed", reason, before_evidence, None, started),
                    False,
                    True,
                )

            proxy = await self._start_proxy()
            try:
                probe_context = await self._new_context(browser, proxy.server_url or "")
                await probe_context.route("**/*", guard)
                probe_page = await probe_context.new_page()
                await probe_page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )
                if is_cancelled():
                    raise _ProbeCancelled
                current_url = normalized_http_url(probe_page.url)
                before_snapshot = await probe_page.evaluate(
                    INTERACTION_STATE_SCRIPT, {"targetIds": target_ids}
                )
                before = _sanitize_snapshot(before_snapshot)
                before_evidence = _evidence(before_snapshot, candidate)

                if blocked_reasons:
                    outcome, reason = "blocked", blocked_reasons[0]
                elif current_url != normalized_http_url(page_url):
                    outcome, reason = "blocked", "out_of_page_navigation"
                    blocked_reasons.append(reason)
                elif _fingerprint(before) != _fingerprint(baseline):
                    outcome, reason = "failed", "initial_state_mismatch"
                else:
                    locator = probe_page.get_by_role(
                        candidate.role or "",
                        name=candidate.name or "",
                        exact=True,
                    )
                    count = await locator.count()
                    if count != 1:
                        outcome, reason = "skipped", "ambiguous_control"
                    else:
                        tag_matches = await locator.locator(
                            f"xpath=self::{candidate.tag_name}"
                        ).count() == 1
                        inside_form = (
                            await locator.locator("xpath=ancestor::form").count() > 0
                            or await locator.get_attribute("form") is not None
                        )
                        inside_tablist = await locator.locator(
                            "xpath=ancestor::*[@role='tablist']"
                        ).count() > 0
                        current_expanded = await locator.get_attribute("aria-expanded")
                        current_selected = await locator.get_attribute("aria-selected")
                        current_type = (await locator.get_attribute("type") or "").lower()
                        verified = (
                            tag_matches
                            and not inside_form
                            and (current_expanded == str(candidate.attributes.get("aria-expanded", "")) or candidate.action_type == "tab")
                            and (current_selected == str(candidate.attributes.get("aria-selected", "")) or candidate.action_type == "disclosure")
                            and (candidate.action_type != "tab" or inside_tablist)
                            and (candidate.tag_name != "button" or current_type == "button")
                        )
                        if not verified or not await locator.is_visible() or not await locator.is_enabled():
                            outcome, reason = "skipped", "candidate_changed_or_ambiguous"
                        else:
                            if is_cancelled():
                                raise _ProbeCancelled
                            try:
                                await locator.click(timeout=timeout_ms)
                                if is_cancelled():
                                    cancelled = True
                                    outcome, reason = "failed", "cancelled_before_completion"
                                else:
                                    after_snapshot = await probe_page.evaluate(
                                        INTERACTION_STATE_SCRIPT, {"targetIds": target_ids}
                                    )
                                    after = _sanitize_snapshot(after_snapshot)
                                    changes = _state_diff(before, after)
                                    after_evidence = _evidence(after_snapshot, candidate, changes)
                                    if blocked_reasons:
                                        outcome, reason = "blocked", blocked_reasons[0]
                                    elif normalized_http_url(probe_page.url) != normalized_http_url(page_url):
                                        outcome, reason = "blocked", "out_of_page_navigation"
                                    elif _fingerprint(before) == _fingerprint(after):
                                        outcome, reason = "failed", "no_state_change"
                                    else:
                                        outcome, reason = "observed", None
                            except PlaywrightError as exc:
                                if blocked_reasons:
                                    outcome, reason = "blocked", blocked_reasons[0]
                                elif is_cancelled():
                                    cancelled = True
                                    outcome, reason = "failed", "cancelled_before_completion"
                                else:
                                    outcome, reason = "failed", "interaction_timeout" if "Timeout" in type(exc).__name__ else "browser_interaction_error"
            finally:
                if probe_context is not None:
                    await probe_context.close()
                await proxy.close()

            if cancelled:
                return ProbeResult(
                    self._record(page_id, candidate, "failed", reason, before_evidence, after_evidence, started),
                    False,
                    True,
                )

            if not is_cancelled():
                restored = await self._verify_restoration(
                    browser, page_url, target_ids, baseline, timeout_ms, is_cancelled
                )
            if is_cancelled():
                cancelled = True
                outcome, reason = "failed", "cancelled_before_completion"
            elif outcome == "observed" and not restored:
                outcome, reason = "failed", "restoration_unverified"
            elif blocked_reasons:
                outcome, reason = "blocked", blocked_reasons[0]

        except _ProbeCancelled:
            cancelled = True
            outcome, reason = "failed", "cancelled_before_interaction"
        except asyncio.CancelledError:
            cancelled = True
            outcome, reason = "failed", "cancelled_before_completion"
        except PlaywrightError as exc:
            if is_cancelled():
                cancelled = True
                outcome, reason = "failed", "cancelled_before_completion"
            else:
                outcome = "blocked" if blocked_reasons else "failed"
                reason = blocked_reasons[0] if blocked_reasons else (
                    "interaction_timeout" if "Timeout" in type(exc).__name__ else "browser_interaction_error"
                )
            if not is_cancelled():
                try:
                    restored = await self._verify_restoration(
                        browser, page_url, target_ids, baseline, timeout_ms, is_cancelled
                    )
                except Exception:
                    restored = False
        except Exception:
            logger.exception("interaction_probe_failed", extra={"page_id": str(page_id), "action_type": candidate.action_type})
            if is_cancelled():
                cancelled = True
                outcome, reason = "failed", "cancelled_before_completion"
            else:
                outcome, reason = ("blocked", blocked_reasons[0]) if blocked_reasons else ("failed", "browser_interaction_error")
            if not is_cancelled():
                try:
                    restored = await self._verify_restoration(
                        browser, page_url, target_ids, baseline, timeout_ms, is_cancelled
                    )
                except Exception:
                    restored = False

        return ProbeResult(
            self._record(page_id, candidate, outcome, reason, before_evidence, after_evidence, started),
            restored,
            cancelled or is_cancelled(),
        )

    async def _start_proxy(self):
        # Each probe gets its own loopback proxy, while DNS pinning continues to
        # use the same validated resolver as the exploration engine.
        from app.services.safe_egress_proxy import SafeEgressProxy

        proxy = SafeEgressProxy(self.address_resolver)
        await proxy.start()
        return proxy

    async def _verify_restoration(
        self,
        browser: Browser,
        page_url: str,
        target_ids: list[str],
        baseline: dict[str, Any],
        timeout_ms: int,
        is_cancelled: Callable[[], bool],
    ) -> bool:
        proxy = await self._start_proxy()
        context: BrowserContext | None = None
        blocked: list[str] = []
        try:
            context = await self._new_context(browser, proxy.server_url or "")

            async def restore_guard(route: Any) -> None:
                request = route.request
                try:
                    if is_cancelled():
                        raise UnsafeTargetUrl("cancelled")
                    await self.url_validator(request.url)
                    if request.method not in {"GET", "HEAD"}:
                        raise UnsafeTargetUrl("non_read_only_method")
                    if UNSAFE_REQUEST_PATH.search(urlsplit(request.url).path):
                        raise UnsafeTargetUrl("unsafe_request_path")
                    if request.is_navigation_request() and (
                        not same_origin(page_url, request.url)
                        or normalized_http_url(request.url) != normalized_http_url(page_url)
                    ):
                        raise UnsafeTargetUrl("out_of_page_navigation")
                except Exception:
                    blocked.append("restore_request_blocked")
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()

            await context.route("**/*", restore_guard)
            page = await context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_ms)
            if normalized_http_url(page.url) != normalized_http_url(page_url):
                return False
            snapshot = await page.evaluate(INTERACTION_STATE_SCRIPT, {"targetIds": target_ids})
            return not blocked and _fingerprint(_sanitize_snapshot(snapshot)) == _fingerprint(baseline)
        except Exception:
            return False
        finally:
            if context is not None:
                await context.close()
            await proxy.close()

    @staticmethod
    def _record(
        page_id: UUID,
        candidate: ProbeCandidate,
        outcome: ObservationOutcome,
        reason: str | None,
        before: dict[str, Any],
        after: dict[str, Any] | None,
        started: float,
    ) -> dict[str, Any]:
        return _base_record(
            page_id,
            candidate,
            outcome,
            reason[:160] if reason else None,
            before,
            after,
            (time.monotonic() - started) * 1000,
        )
