import pytest
from collections.abc import Generator
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool
from uuid import uuid4

from app.db.base import Base
from app.models.exploration import Exploration, ExplorationPage, ObservedElement
from app.schemas.analysis import AnalysisEvidence, WorkflowStep, WorkflowSuggestion
from app.services.test_planner import InvalidScenario, compile_scenario
from app import models  # noqa: F401 - registers metadata


@pytest.fixture
def planner_database() -> Generator[tuple[sessionmaker[Session], object, dict], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    exploration_id = uuid4()
    with factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url="https://shop.example/",
            status="completed",
            max_pages=5,
            max_depth=1,
            pages_discovered=2,
        )
        root = ExplorationPage(
            exploration_id=exploration_id,
            url="https://shop.example/",
            title="Shop",
            depth=0,
            http_status=200,
            element_count=1,
        )
        catalog = ExplorationPage(
            exploration_id=exploration_id,
            url="https://shop.example/catalog",
            title="Catalog",
            depth=1,
            http_status=200,
            element_count=1,
        )
        session.add_all([exploration, root, catalog])
        session.flush()
        root_link = ObservedElement(
            page_id=root.id,
            ordinal=0,
            kind="link",
            tag_name="a",
            role="link",
            accessible_name="Catalog",
            selector="a#catalog",
            attributes={"id": "catalog", "href": "https://shop.example/catalog", "inside_form": False},
        )
        sort_button = ObservedElement(
            page_id=catalog.id,
            ordinal=0,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Sort products",
            selector="button#sort",
            attributes={"id": "sort", "type": "button"},
        )
        session.add_all([root_link, sort_button])
        session.commit()
        ids = {
            "root_page": root.id,
            "catalog_page": catalog.id,
            "root_link": root_link.id,
            "sort_button": sort_button.id,
        }
    try:
        yield factory, exploration_id, ids
    finally:
        engine.dispose()


def _pages(session_factory, exploration_id):
    with session_factory() as session:
        return list(
            session.scalars(
                select(ExplorationPage)
                .options(selectinload(ExplorationPage.elements))
                .where(ExplorationPage.exploration_id == exploration_id)
                .order_by(ExplorationPage.depth)
            )
        )


def test_planner_compiles_only_saved_navigation_and_visible_assertions(planner_database) -> None:
    session_factory, exploration_id, ids = planner_database
    workflow = WorkflowSuggestion(
        title="Open the product catalog",
        objective="Verify that catalog navigation reaches the saved catalog.",
        relevant_page_ids=[ids["root_page"], ids["catalog_page"]],
        steps=[
            WorkflowStep(
                instruction="Open the Catalog link.",
                evidence=[AnalysisEvidence(page_id=ids["root_page"], element_ids=[ids["root_link"]])],
            ),
            WorkflowStep(
                instruction="Verify the sorting control is visible.",
                evidence=[AnalysisEvidence(page_id=ids["catalog_page"], element_ids=[ids["sort_button"]])],
            ),
        ],
        expected_outcome="The catalog page displays the sorting control.",
    )

    compiled = compile_scenario(0, workflow, _pages(session_factory, exploration_id))

    assert [step.action["kind"] for step in compiled.steps] == ["click_link", "assert_visible"]
    assert compiled.steps[0].action["target_url"] == "https://shop.example/catalog"
    assert compiled.steps[1].expected_evidence["visible"] is True


def test_planner_rejects_unsupported_actions_and_unpersisted_evidence(planner_database) -> None:
    session_factory, exploration_id, ids = planner_database
    pages = _pages(session_factory, exploration_id)
    base = {
        "title": "Unsafe generated action",
        "objective": "Exercise a saved element.",
        "relevant_page_ids": [ids["root_page"]],
        "steps": [
            {
                "instruction": "Submit the form with a destructive action.",
                "evidence": [{"page_id": ids["root_page"], "element_ids": [ids["root_link"]]}],
            }
        ],
        "expected_outcome": "The form is submitted.",
    }
    unsupported = WorkflowSuggestion.model_validate(base)
    with pytest.raises(InvalidScenario, match="state-changing or unsupported outcome"):
        compile_scenario(0, unsupported, pages)

    invalid_evidence = WorkflowSuggestion.model_validate(
        {
            **base,
            "steps": [
                {
                    "instruction": "Verify an unknown control is visible.",
                    "evidence": [{"page_id": ids["root_page"], "element_ids": [uuid4()]}],
                }
            ],
            "expected_outcome": "The saved control is visible.",
        }
    )
    with pytest.raises(InvalidScenario, match="unsaved element"):
        compile_scenario(0, invalid_evidence, pages)


def test_planner_rejects_selector_injection_and_destructive_outcomes(planner_database) -> None:
    session_factory, exploration_id, ids = planner_database
    pages = _pages(session_factory, exploration_id)
    pages[0].elements[0].selector = "a#catalog, form"
    selector_scenario = WorkflowSuggestion(
        title="Verify the catalog link",
        objective="Check the saved link.",
        relevant_page_ids=[ids["root_page"]],
        steps=[
            WorkflowStep(
                instruction="Verify the Catalog link is visible.",
                evidence=[AnalysisEvidence(page_id=ids["root_page"], element_ids=[ids["root_link"]])],
            )
        ],
        expected_outcome="The link is visible.",
    )
    with pytest.raises(InvalidScenario, match="safe supported locator"):
        compile_scenario(0, selector_scenario, pages)

    unsafe_outcome = selector_scenario.model_copy(
        update={
            "steps": [
                WorkflowStep(
                    instruction="Verify the Catalog link is visible.",
                    evidence=[AnalysisEvidence(page_id=ids["root_page"], element_ids=[ids["root_link"]])],
                )
            ],
            "expected_outcome": "The account is deleted.",
        }
    )
    pages[0].elements[0].selector = "a#catalog"
    with pytest.raises(InvalidScenario, match="state-changing or unsupported outcome"):
        compile_scenario(0, unsafe_outcome, pages)


def test_planner_allows_only_explicit_static_internal_navigation(planner_database) -> None:
    session_factory, exploration_id, ids = planner_database
    pages = _pages(session_factory, exploration_id)
    workflow = WorkflowSuggestion(
        title="Open the product catalog",
        objective="Verify that the catalog page opens.",
        relevant_page_ids=[ids["root_page"], ids["catalog_page"]],
        steps=[
            WorkflowStep(
                instruction="Click the Catalog link.",
                evidence=[AnalysisEvidence(page_id=ids["root_page"], element_ids=[ids["root_link"]])],
            )
        ],
        expected_outcome="The catalog page is displayed.",
    )

    compiled = compile_scenario(0, workflow, pages)

    assert compiled.steps[0].action["link_name"] == "catalog"
    assert compiled.steps[0].action["target_url"] == "https://shop.example/catalog"


@pytest.mark.parametrize(
    ("name", "href", "inside_form", "role", "reason"),
    [
        ("Delete account", "https://shop.example/delete-account", False, "link", "blocked or state-changing"),
        ("Proceed", "https://shop.example/catalog", False, "link", "navigation label"),
        ("Catalog", "https://shop.example/catalog?next=delete", False, "link", "content route"),
        ("Catalog", "https://shop.example/catalog", True, "link", "non-form evidence"),
        ("Catalog", "https://shop.example/catalog", False, "button", "ambiguous or lacks verified"),
    ],
)
def test_planner_rejects_destructive_ambiguous_or_unverified_links(
    planner_database, name, href, inside_form, role, reason
) -> None:
    session_factory, exploration_id, ids = planner_database
    pages = _pages(session_factory, exploration_id)
    link = pages[0].elements[0]
    link.accessible_name = name
    link.attributes = {"id": "catalog", "href": href, "inside_form": inside_form}
    link.role = role
    workflow = WorkflowSuggestion(
        title="Open the saved page",
        objective="Inspect an internal page.",
        relevant_page_ids=[ids["root_page"], ids["catalog_page"]],
        steps=[
            WorkflowStep(
                instruction="Click the saved navigation link.",
                evidence=[AnalysisEvidence(page_id=ids["root_page"], element_ids=[ids["root_link"]])],
            )
        ],
        expected_outcome="The saved internal page is displayed.",
    )

    with pytest.raises(InvalidScenario, match=reason):
        compile_scenario(0, workflow, pages)
