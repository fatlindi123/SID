"""Validation and bounded snapshots for observation-backed test steps."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.test_navigation import is_safe_navigation_target
from app.core.url_security import UnsafeTargetUrl, normalized_http_url
from app.models.exploration import ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation


_SAFE_NAME = re.compile(
    r"^(?!.*(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}))(?!.*\b(?:password|credential|token|secret)\b).{1,120}$",
    re.IGNORECASE,
)


class InvalidInteractionObservation(ValueError):
    """A saved interaction is not safe or current enough to execute as a test step."""


@dataclass(frozen=True)
class ValidatedInteractionObservation:
    observation: InteractionObservation
    page: ExplorationPage
    element: ObservedElement
    attribute: str
    before_value: bool
    observed_after_value: bool
    name: str
    role: str
    tag_name: str
    page_url: str
    controls: str | None

    def state(self, value: bool) -> dict[str, bool | None]:
        if self.attribute == "aria-selected":
            return {"selected": value, "expanded": None}
        return {"selected": None, "expanded": value}

    def evidence_snapshot(self, expected_value: bool) -> dict[str, Any]:
        return {
            "observation_id": str(self.observation.id),
            "probe_key": self.observation.probe_key,
            "interaction_type": self.observation.action_type,
            "page": {
                "id": str(self.page.id),
                "url": self.page_url,
                "title": (self.page.title or "")[:160],
            },
            "control": {
                "element_id": str(self.element.id),
                "element_ordinal": self.element.ordinal,
                "tag_name": self.tag_name,
                "role": self.role,
                "name": self.name,
                "controls": self.controls,
            },
            "before_state": self.state(self.before_value),
            "observed_after_state": self.state(self.observed_after_value),
            "expected_state": {
                "attribute": self.attribute,
                "value": expected_value,
                "user_confirmed": True,
            },
        }


def _target(evidence: Any) -> dict[str, Any]:
    if not isinstance(evidence, dict) or not isinstance(evidence.get("target"), dict):
        raise InvalidInteractionObservation("The saved observation has no bounded control evidence.")
    return evidence["target"]


def _state_value(target: dict[str, Any], attribute: str) -> bool:
    value = target.get("selected" if attribute == "aria-selected" else "expanded")
    if not isinstance(value, bool):
        raise InvalidInteractionObservation("The saved observation has no complete before/after state.")
    return value


def _probe_key_matches(observation: InteractionObservation, element: ObservedElement) -> bool:
    source = "\0".join(
        (
            observation.action_type,
            str(element.ordinal),
            str(element.tag_name),
            str(element.role or ""),
            str(element.accessible_name or ""),
            str(element.selector or ""),
        )
    )
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest() == observation.probe_key


def validate_interaction_observation(
    observation: InteractionObservation,
    exploration_id: UUID,
    pages: list[ExplorationPage],
) -> ValidatedInteractionObservation:
    """Revalidate a persisted observed result against its current page/control rows."""
    if observation.outcome != "observed" or observation.reason is not None:
        raise InvalidInteractionObservation("Only completed observed interactions are eligible.")
    if observation.exploration_id != exploration_id:
        raise InvalidInteractionObservation("The observation belongs to another exploration.")
    if observation.action_type not in {"tab", "disclosure"}:
        raise InvalidInteractionObservation("This interaction type is not supported for test execution.")

    page = next((item for item in pages if item.id == observation.page_id), None)
    if page is None or page.exploration_id != exploration_id:
        raise InvalidInteractionObservation("The observation page is no longer saved in this exploration.")
    if not isinstance(observation.before_evidence, dict) or not isinstance(observation.after_evidence, dict):
        raise InvalidInteractionObservation("The saved observation evidence is incomplete.")
    try:
        page_url = normalized_http_url(page.url)
        before_url = normalized_http_url(str(observation.before_evidence.get("url", "")))
        after_url = normalized_http_url(str(observation.after_evidence.get("url", "")))
    except (UnsafeTargetUrl, TypeError, ValueError) as exc:
        raise InvalidInteractionObservation("The saved observation page URL is invalid.") from exc
    if (
        before_url != page_url
        or after_url != page_url
        or not is_safe_navigation_target(page_url)
    ):
        raise InvalidInteractionObservation("The observation is not bound to an approved saved page route.")

    element = next((item for item in page.elements if item.ordinal == observation.element_ordinal), None)
    if element is None or not _probe_key_matches(observation, element):
        raise InvalidInteractionObservation("The saved control evidence is stale or no longer matches its page element.")
    attributes = element.attributes or {}
    if (
        attributes.get("inside_form") is not False
        or attributes.get("disabled")
        or str(attributes.get("aria-disabled", "")).casefold() == "true"
        or element.tag_name.lower() not in {"button", "div"}
        or not isinstance(element.selector, str)
        or not element.selector.strip()
    ):
        raise InvalidInteractionObservation("The saved control is not an eligible non-form interaction.")
    if element.tag_name.lower() == "button" and str(attributes.get("type", "")).casefold() != "button":
        raise InvalidInteractionObservation("Submit and reset controls cannot be used as interaction steps.")

    before = _target(observation.before_evidence)
    after = _target(observation.after_evidence)
    for evidence in (observation.before_evidence, observation.after_evidence):
        saved_title = evidence.get("title")
        if saved_title is not None and saved_title != (page.title or "")[:160]:
            raise InvalidInteractionObservation("The observed page title no longer matches its saved page.")
    expected_role = "tab" if observation.action_type == "tab" else "button"
    expected_attribute = "aria-selected" if observation.action_type == "tab" else "aria-expanded"
    if observation.action_type == "tab":
        if element.role != "tab" or attributes.get("inside_tablist") is not True:
            raise InvalidInteractionObservation("The saved tab is missing verified tablist semantics.")
    elif (
        element.role != "button"
        or attributes.get("controls_valid") is not True
        or not isinstance(attributes.get("aria-controls"), str)
    ):
        raise InvalidInteractionObservation("The saved disclosure has no verified controlled region.")

    name = element.accessible_name or ""
    if not _SAFE_NAME.fullmatch(name) or name != before.get("name") or name != after.get("name"):
        raise InvalidInteractionObservation("The saved control name is missing, sensitive, or stale.")
    for target in (before, after):
        if (
            str(target.get("tag_name", "")).casefold() != element.tag_name.casefold()
            or str(target.get("role", "")).casefold() != expected_role
        ):
            raise InvalidInteractionObservation("The saved control role or tag no longer matches its observation.")

    before_value = _state_value(before, expected_attribute)
    after_value = _state_value(after, expected_attribute)
    if before_value == after_value:
        raise InvalidInteractionObservation("The saved observation did not verify a state transition.")
    saved_value = attributes.get("aria-selected" if observation.action_type == "tab" else "aria-expanded")
    if str(saved_value).casefold() != str(before_value).casefold():
        raise InvalidInteractionObservation("The saved before-state no longer matches the persisted element.")

    return ValidatedInteractionObservation(
        observation=observation,
        page=page,
        element=element,
        attribute=expected_attribute,
        before_value=before_value,
        observed_after_value=after_value,
        name=name,
        role=expected_role,
        tag_name=element.tag_name.lower(),
        page_url=page_url,
        controls=(str(attributes.get("aria-controls")) if attributes.get("aria-controls") is not None else None),
    )


def observation_ids_from_scenario(scenario: Any) -> set[UUID]:
    """Read IDs from validated schema objects or persisted JSON without trusting them."""
    steps = scenario.get("steps", []) if isinstance(scenario, dict) else getattr(scenario, "steps", [])
    output: set[UUID] = set()
    for step in steps if isinstance(steps, list) else []:
        selection = step.get("interaction_observation") if isinstance(step, dict) else getattr(step, "interaction_observation", None)
        value = selection.get("observation_id") if isinstance(selection, dict) else getattr(selection, "observation_id", None)
        if value is None:
            continue
        try:
            output.add(value if isinstance(value, UUID) else UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return output


def load_interaction_observations(session: Session, scenario: Any) -> dict[UUID, InteractionObservation]:
    ids = observation_ids_from_scenario(scenario)
    if not ids:
        return {}
    rows = session.scalars(select(InteractionObservation).where(InteractionObservation.id.in_(ids)))
    return {row.id: row for row in rows}
