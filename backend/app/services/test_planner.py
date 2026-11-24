"""Compile persisted AI suggestions into a small, safe browser-action language."""

import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.core.url_security import UnsafeTargetUrl, normalized_http_url, same_origin
from app.core.test_navigation import is_safe_navigation_target, normalized_navigation_label
from app.models.exploration import ExplorationPage, ObservedElement
from app.schemas.analysis import AnalysisResult, WorkflowStep, WorkflowSuggestion
from app.schemas.test_execution import UserWorkflowSuggestion
from app.services.test_interactions import (
    InvalidInteractionObservation,
    validate_interaction_observation,
)
from app.models.interaction_observation import InteractionObservation


SAFE_SELECTOR = re.compile(
    r'^[a-z][a-z0-9-]*(?:#[A-Za-z_-][A-Za-z0-9_-]*|'
    r'\[role="[A-Za-z][A-Za-z0-9_-]*"\]|'
    r'\[data-testid="[A-Za-z0-9_. -]{1,100}"\])?$'
)
NAVIGATION_INSTRUCTION = re.compile(r"^(?:open|visit|go to|navigate to|load)\b", re.IGNORECASE)
CLICK_INSTRUCTION = re.compile(r"^(?:click|follow|open)\b", re.IGNORECASE)
VISIBLE_INSTRUCTION = re.compile(
    r"^(?:verify|check|assert|confirm|ensure)\b.*\b(?:visible|displayed|shown|present)\b",
    re.IGNORECASE,
)
UNSUPPORTED_SIDE_EFFECT = re.compile(
    r"\b(delete|deleted|deletes|deleting|remove|removed|removes|removing|destroy|destroyed|"
    r"destroying|logout|signout|purchase|purchased|purchasing|checkout|payment|pay|paid|"
    r"subscribe|subscribed|subscription|unsubscribe|cancel|cancelled|canceled|confirm|confirmed|"
    r"submit|submitted|submitting|send|sent|sending|update|updated|updating|create|created|creating|"
    r"buy|bought|transfer|transferred|fill|filled|filling|type|typed|typing|enter|entered|entering|"
    r"execute|executed|executing|javascript|"
    r"script|credential\w*|password)\b",
    re.IGNORECASE,
)
MULTI_COMMAND = re.compile(r"\b(and then|then|after that|finally|also)\b", re.IGNORECASE)
UNSAFE_PATH = re.compile(
    r"(?:^|[-_/?&=])(delet\w*|remov\w*|destroy\w*|logout|signout|purchas\w*|checkout|payment|"
    r"subscrib\w*|unsubscrib\w*|cancel\w*|confirm\w*|submi\w*)(?:[-_/?#&=]|$)",
    re.IGNORECASE,
)


class InvalidScenario(ValueError):
    """A saved AI scenario cannot be executed with the supported safe actions."""


@dataclass(frozen=True)
class CompiledStep:
    ordinal: int
    instruction: str
    action: dict[str, Any]
    expected_evidence: dict[str, Any]


@dataclass(frozen=True)
class CompiledScenario:
    scenario_index: int
    scenario: dict[str, Any]
    steps: tuple[CompiledStep, ...]


def _selector(element: ObservedElement) -> str:
    selector = element.selector
    if not isinstance(selector, str) or len(selector) > 300 or not SAFE_SELECTOR.fullmatch(selector):
        raise InvalidScenario("A saved element has no safe supported locator.")
    return selector


def _page_url(page: ExplorationPage) -> str:
    try:
        return normalized_http_url(page.url)
    except UnsafeTargetUrl as exc:
        raise InvalidScenario("A referenced saved page has an invalid URL.") from exc


def list_scenarios(
    analysis_result: dict[str, Any] | None,
    pages: list[ExplorationPage],
) -> list[dict[str, Any]]:
    if not analysis_result:
        return []
    result = AnalysisResult.model_validate(analysis_result)
    output = []
    for index, workflow in enumerate(result.potential_workflows):
        try:
            compiled = compile_scenario(index, workflow, pages)
            output.append(
                {
                    **compiled.scenario,
                    "scenario_index": index,
                    "executable": True,
                    "unavailable_reason": None,
                }
            )
        except InvalidScenario as exc:
            output.append(
                {
                    **workflow.model_dump(mode="json"),
                    "scenario_index": index,
                    "executable": False,
                    "unavailable_reason": str(exc),
                }
            )
    return output


def compile_scenario(
    scenario_index: int,
    workflow: WorkflowSuggestion,
    pages: list[ExplorationPage],
) -> CompiledScenario:
    pages_by_id = {page.id: page for page in pages}
    element_pairs: dict[UUID, tuple[ExplorationPage, ObservedElement]] = {
        element.id: (page, element) for page in pages for element in page.elements
    }
    relevant_ids = set(workflow.relevant_page_ids)
    if not relevant_ids or not relevant_ids.issubset(pages_by_id):
        raise InvalidScenario("The scenario references a page that is no longer saved.")
    if not workflow.steps:
        raise InvalidScenario("The scenario has no executable steps.")
    if any(
        UNSUPPORTED_SIDE_EFFECT.search(text)
        for text in (workflow.title, workflow.objective, workflow.expected_outcome)
    ):
        raise InvalidScenario("The scenario describes a state-changing or unsupported outcome.")

    compiled_steps: list[CompiledStep] = []
    for ordinal, step in enumerate(workflow.steps):
        if UNSUPPORTED_SIDE_EFFECT.search(step.instruction) or MULTI_COMMAND.search(step.instruction):
            raise InvalidScenario(f"Step {ordinal + 1} contains an unsupported action or state-changing behavior.")
        if not step.evidence:
            raise InvalidScenario(f"Step {ordinal + 1} has no persisted page or element evidence.")

        references = []
        for reference in step.evidence:
            page = pages_by_id.get(reference.page_id)
            if page is None or page.id not in relevant_ids:
                raise InvalidScenario(f"Step {ordinal + 1} references a page outside this scenario.")
            if UNSAFE_PATH.search(_page_url(page)):
                raise InvalidScenario(f"Step {ordinal + 1} references a blocked action page.")
            if len(reference.element_ids) != len(set(reference.element_ids)):
                raise InvalidScenario(f"Step {ordinal + 1} contains duplicate element evidence.")
            elements: list[ObservedElement] = []
            for element_id in reference.element_ids:
                pair = element_pairs.get(element_id)
                if pair is None or pair[0].id != reference.page_id:
                    raise InvalidScenario(f"Step {ordinal + 1} references an unsaved element.")
                elements.append(pair[1])
            references.append((page, elements))

        if CLICK_INSTRUCTION.match(step.instruction) and any(elements for _, elements in references):
            if len(references) != 1 or len(references[0][1]) != 1:
                raise InvalidScenario(f"Step {ordinal + 1} must reference exactly one saved link.")
            source_page, elements = references[0]
            element = elements[0]
            if element.kind != "link" or element.tag_name.lower() != "a":
                raise InvalidScenario(f"Step {ordinal + 1} may only click a saved link.")
            if element.role != "link" or element.attributes.get("inside_form") is not False:
                raise InvalidScenario(f"Step {ordinal + 1} link is ambiguous or lacks verified non-form evidence.")
            if any(
                isinstance(value, str) and UNSUPPORTED_SIDE_EFFECT.search(value)
                for value in (element.accessible_name, element.label, element.text)
            ):
                raise InvalidScenario(f"Step {ordinal + 1} link describes a blocked or state-changing action.")
            link_name = normalized_navigation_label(element.accessible_name)
            if link_name is None:
                raise InvalidScenario(f"Step {ordinal + 1} link name is not an explicitly supported navigation label.")
            selector = _selector(element)
            href = element.attributes.get("href")
            if not isinstance(href, str):
                raise InvalidScenario(f"Step {ordinal + 1} link has no saved destination.")
            try:
                target_url = normalized_http_url(href)
            except UnsafeTargetUrl as exc:
                raise InvalidScenario(f"Step {ordinal + 1} link destination is not a safe URL.") from exc
            if not is_safe_navigation_target(href):
                raise InvalidScenario(f"Step {ordinal + 1} link destination is not an explicitly supported content route.")
            target_page = next(
                (page for page in pages if _page_url(page) == target_url), None
            )
            if target_page is None or target_page.id not in relevant_ids:
                raise InvalidScenario(f"Step {ordinal + 1} link does not lead to a saved scenario page.")
            if not same_origin(source_page.url, target_url):
                raise InvalidScenario(f"Step {ordinal + 1} link leaves the explored site origin.")
            if not is_safe_navigation_target(_page_url(source_page)):
                raise InvalidScenario(f"Step {ordinal + 1} source page is not an explicitly supported content route.")
            if UNSAFE_PATH.search(target_url):
                raise InvalidScenario(f"Step {ordinal + 1} link targets a blocked action path.")
            compiled_steps.append(
                CompiledStep(
                    ordinal=ordinal,
                    instruction=step.instruction,
                    action={
                        "kind": "click_link",
                        "page_id": str(source_page.id),
                        "page_url": _page_url(source_page),
                        "target_page_id": str(target_page.id),
                        "target_url": target_url,
                        "target_title": target_page.title,
                        "element_id": str(element.id),
                        "selector": selector,
                        "link_name": link_name,
                    },
                    expected_evidence={
                        "page_id": str(target_page.id),
                        "url": target_url,
                        "title": target_page.title,
                        "element_id": str(element.id),
                        "selector": selector,
                    },
                )
            )
            continue

        if VISIBLE_INSTRUCTION.match(step.instruction):
            if len(references) != 1 or len(references[0][1]) != 1:
                raise InvalidScenario(f"Step {ordinal + 1} must reference exactly one saved element.")
            page, elements = references[0]
            element = elements[0]
            selector = _selector(element)
            compiled_steps.append(
                CompiledStep(
                    ordinal=ordinal,
                    instruction=step.instruction,
                    action={
                        "kind": "assert_visible",
                        "page_id": str(page.id),
                        "page_url": _page_url(page),
                        "page_title": page.title,
                        "element_id": str(element.id),
                        "selector": selector,
                    },
                    expected_evidence={
                        "page_id": str(page.id),
                        "url": _page_url(page),
                        "title": page.title,
                        "element_id": str(element.id),
                        "selector": selector,
                        "visible": True,
                    },
                )
            )
            continue

        if NAVIGATION_INSTRUCTION.match(step.instruction):
            if len(references) != 1 or references[0][1]:
                raise InvalidScenario(f"Step {ordinal + 1} must reference one saved page without element actions.")
            page, _ = references[0]
            if page.id not in relevant_ids:
                raise InvalidScenario(f"Step {ordinal + 1} references a page outside this scenario.")
            if not is_safe_navigation_target(_page_url(page)):
                raise InvalidScenario(f"Step {ordinal + 1} page is not an explicitly supported content route.")
            compiled_steps.append(
                CompiledStep(
                    ordinal=ordinal,
                    instruction=step.instruction,
                    action={
                        "kind": "navigate",
                        "page_id": str(page.id),
                        "url": _page_url(page),
                        "title": page.title,
                    },
                    expected_evidence={
                        "page_id": str(page.id),
                        "url": _page_url(page),
                        "title": page.title,
                    },
                )
            )
            continue

        raise InvalidScenario(
            f"Step {ordinal + 1} uses an unsupported action. Supported steps are saved-page navigation, "
            "clicking an internal saved link, and checking one saved element is visible."
        )

    return CompiledScenario(
        scenario_index=scenario_index,
        scenario=workflow.model_dump(mode="json"),
        steps=tuple(compiled_steps),
    )


def compile_user_scenario(
    scenario: UserWorkflowSuggestion,
    exploration_id: UUID,
    pages: list[ExplorationPage],
    observations: dict[UUID, InteractionObservation],
) -> CompiledScenario:
    """Compile manual workflow steps plus explicitly confirmed observations."""
    pages_by_id = {page.id: page for page in pages}
    relevant_ids = set(scenario.relevant_page_ids)
    if not relevant_ids or not relevant_ids.issubset(pages_by_id):
        raise InvalidScenario("The scenario references a page that is no longer saved.")
    if any(
        UNSUPPORTED_SIDE_EFFECT.search(text)
        for text in (scenario.title, scenario.objective, scenario.expected_outcome)
    ):
        raise InvalidScenario("The scenario describes a state-changing or unsupported outcome.")

    compiled_steps: list[CompiledStep] = []
    normalized_steps: list[dict[str, Any]] = []
    seen_observation_ids: set[UUID] = set()
    for ordinal, step in enumerate(scenario.steps):
        if step.interaction_observation is None:
            if step.interaction_evidence_snapshot is not None:
                raise InvalidScenario("An interaction evidence snapshot must reference a selected observation.")
            try:
                workflow = WorkflowSuggestion(
                    title=scenario.title,
                    objective=scenario.objective,
                    relevant_page_ids=scenario.relevant_page_ids,
                    steps=[WorkflowStep(instruction=step.instruction, evidence=step.evidence)],
                    expected_outcome=scenario.expected_outcome,
                )
                single_step = compile_scenario(ordinal, workflow, pages)
            except (InvalidScenario, ValueError) as exc:
                raise InvalidScenario(str(exc)) from exc
            compiled = single_step.steps[0]
            compiled_steps.append(
                CompiledStep(ordinal, compiled.instruction, compiled.action, compiled.expected_evidence)
            )
            normalized_steps.append(single_step.scenario["steps"][0])
            continue

        selection = step.interaction_observation
        if selection.observation_id in seen_observation_ids:
            raise InvalidScenario("An interaction observation can only be used once in a scenario.")
        seen_observation_ids.add(selection.observation_id)
        observation = observations.get(selection.observation_id)
        if observation is None:
            raise InvalidScenario("The selected interaction observation is no longer available.")
        try:
            validated = validate_interaction_observation(
                observation,
                exploration_id,
                pages,
            )
        except InvalidInteractionObservation as exc:
            raise InvalidScenario(str(exc)) from exc
        if validated.page.id not in relevant_ids:
            raise InvalidScenario("The interaction observation page is outside this scenario.")
        if len(step.evidence) != 1:
            raise InvalidScenario("An interaction step must reference exactly its observed control.")
        evidence = step.evidence[0]
        if evidence.page_id != validated.page.id or evidence.element_ids != [validated.element.id]:
            raise InvalidScenario("The interaction step evidence does not match the saved observation control.")
        if selection.expected_state != validated.observed_after_value:
            raise InvalidScenario("Choose and confirm the state recorded after the observed interaction.")
        if selection.expected_state_confirmed is not True:
            raise InvalidScenario("Confirm the expected interaction state before saving this case.")

        instruction = (
            f"Activate the {validated.role} named {validated.name} and verify "
            f"{validated.attribute} is {str(selection.expected_state).lower()}."
        )
        evidence_json = [{"page_id": str(evidence.page_id), "element_ids": [str(item) for item in evidence.element_ids]}]
        snapshot = validated.evidence_snapshot(selection.expected_state)
        normalized_steps.append(
            {
                "instruction": instruction,
                "evidence": evidence_json,
                "interaction_observation": selection.model_dump(mode="json"),
                "interaction_evidence_snapshot": snapshot,
            }
        )
        action = {
            "kind": "interaction_transition",
            "observation_id": str(observation.id),
            "page_id": str(validated.page.id),
            "page_url": validated.page_url,
            "page_title": (validated.page.title or "")[:160],
            "interaction_type": observation.action_type,
            "element_id": str(validated.element.id),
            "element_ordinal": validated.element.ordinal,
            "tag_name": validated.tag_name,
            "role": validated.role,
            "name": validated.name,
            "controls": validated.controls,
            "attribute": validated.attribute,
            "before_value": validated.before_value,
            "expected_value": selection.expected_state,
        }
        expected = {
            "observation_id": str(observation.id),
            "page_id": str(validated.page.id),
            "interaction_type": observation.action_type,
            "attribute": validated.attribute,
            "before_value": validated.before_value,
            "expected_value": selection.expected_state,
            "control": snapshot["control"],
        }
        compiled_steps.append(CompiledStep(ordinal, instruction, action, expected))

    normalized_scenario = {
        "title": scenario.title,
        "objective": scenario.objective,
        "relevant_page_ids": [str(page_id) for page_id in scenario.relevant_page_ids],
        "steps": normalized_steps,
        "expected_outcome": scenario.expected_outcome,
    }
    return CompiledScenario(-1, normalized_scenario, tuple(compiled_steps))


def compile_manual_login_smoke_test(
    page_id: UUID,
    login_button_element_id: UUID,
    pages: list[ExplorationPage],
    exploration_start_url: str,
) -> CompiledScenario:
    """Compile the one supported manual smoke test from persisted SauceDemo evidence."""
    pages_by_id = {page.id: page for page in pages}
    page = pages_by_id.get(page_id)
    if page is None:
        raise InvalidScenario("The selected page is not saved for this exploration.")

    try:
        saved_url = _page_url(page)
        root_url = normalized_http_url(exploration_start_url)
    except UnsafeTargetUrl as exc:
        raise InvalidScenario("The saved login page has an invalid URL.") from exc
    if root_url != "https://www.saucedemo.com/" or saved_url != root_url:
        raise InvalidScenario("The manual smoke test may only use the saved exploration start page.")

    element = next((item for item in page.elements if item.id == login_button_element_id), None)
    attributes = element.attributes if element is not None else {}
    if (
        element is None
        or element.kind != "input"
        or element.tag_name.lower() != "input"
        or element.role != "button"
        or element.accessible_name != "Login"
        or not isinstance(attributes, dict)
        or attributes.get("type") != "submit"
    ):
        raise InvalidScenario("The selected saved element is not the observed Login button.")

    selector = _selector(element)
    if attributes.get("id") and selector != f"input#{attributes['id']}":
        raise InvalidScenario("The saved Login button locator does not match its persisted ID.")

    title = "Manual SauceDemo login page smoke test"
    objective = "Verify the explored SauceDemo login page loads and its saved Login button is visible."
    expected_outcome = "The saved Login button is visible on the explored SauceDemo login page."
    navigate_instruction = "Navigate to the saved SauceDemo login page."
    assertion_instruction = "Assert that the saved Login button is visible."
    scenario = {
        "source": "manual",
        "title": title,
        "objective": objective,
        "expected_outcome": expected_outcome,
        "relevant_page_ids": [str(page.id)],
        "page_id": str(page.id),
        "login_button_element_id": str(element.id),
        "steps": [
            {
                "instruction": navigate_instruction,
                "evidence": [{"page_id": str(page.id), "element_ids": []}],
            },
            {
                "instruction": assertion_instruction,
                "evidence": [{"page_id": str(page.id), "element_ids": [str(element.id)]}],
            },
        ],
    }
    return CompiledScenario(
        scenario_index=-1,
        scenario=scenario,
        steps=(
            CompiledStep(
                ordinal=0,
                instruction=navigate_instruction,
                action={"kind": "navigate", "page_id": str(page.id), "url": saved_url, "title": page.title},
                expected_evidence={"page_id": str(page.id), "url": saved_url, "title": page.title},
            ),
            CompiledStep(
                ordinal=1,
                instruction=assertion_instruction,
                action={
                    "kind": "assert_visible",
                    "page_id": str(page.id),
                    "page_url": saved_url,
                    "page_title": page.title,
                    "element_id": str(element.id),
                    "selector": selector,
                },
                expected_evidence={
                    "page_id": str(page.id),
                    "url": saved_url,
                    "title": page.title,
                    "element_id": str(element.id),
                    "selector": selector,
                    "visible": True,
                },
            ),
        ),
    )
