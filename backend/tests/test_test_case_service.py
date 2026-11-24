from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage, ObservedElement
from app.models.test_case import TestCase
from app.schemas.analysis import (
    AnalysisEvidence,
    AnalysisResult,
    PageSummary,
    WebsitePurpose,
    WorkflowStep,
    WorkflowSuggestion,
)
from app.services.test_case_service import AnalysisNotReady, sync_ai_test_cases
from app import models  # noqa: F401 - register all model metadata


@pytest.fixture
def case_database():
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
            max_pages=10,
            max_depth=2,
        )
        page = ExplorationPage(
            exploration=exploration,
            url=exploration.start_url,
            title="Example Shop",
            depth=0,
            http_status=200,
        )
        element = ObservedElement(
            page=page,
            ordinal=0,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Continue",
            selector="button#continue",
            attributes={"id": "continue", "type": "button"},
        )
        session.add_all([exploration, page, element])
        session.flush()
        evidence = AnalysisEvidence(page_id=page.id, element_ids=[element.id])
        workflow = WorkflowSuggestion(
            title="Verify the Continue button",
            objective="Check that the saved primary action is available.",
            relevant_page_ids=[page.id],
            steps=[WorkflowStep(instruction="Verify the Continue button is visible.", evidence=[evidence])],
            expected_outcome="The saved Continue button is visible.",
        )
        result = AnalysisResult(
            website_purpose=WebsitePurpose(
                interpretation="The page presents a primary action.", evidence=[evidence]
            ),
            page_summaries=[
                PageSummary(page_id=page.id, interpretation="The page has a Continue button.", evidence=[evidence])
            ],
            main_navigation=[],
            important_interactive_elements=[],
            forms_and_fields=[],
            potential_workflows=[workflow],
        )
        analysis = ExplorationAnalysis(
            exploration=exploration,
            status="completed",
            attempt_count=1,
            result=result.model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.commit()
        ids = (exploration.id, page.id, element.id, analysis.id)
    try:
        yield factory, ids
    finally:
        engine.dispose()


def _sync(factory, exploration_id, analysis_id):
    with factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        pages = list(session.scalars(select(ExplorationPage).where(ExplorationPage.exploration_id == exploration_id)))
        cases = sync_ai_test_cases(session, analysis, pages)
        session.commit()
        return [case.id for case in cases]


def test_analysis_sync_produces_stable_ids_and_no_duplicate_cases(case_database) -> None:
    factory, (exploration_id, _page_id, _element_id, analysis_id) = case_database

    first_ids = _sync(factory, exploration_id, analysis_id)
    second_ids = _sync(factory, exploration_id, analysis_id)

    assert first_ids == second_ids
    with factory() as session:
        cases = list(session.scalars(select(TestCase).where(TestCase.exploration_id == exploration_id)))
        assert len(cases) == 1
        assert cases[0].id == first_ids[0]
        assert cases[0].source == "ai_generated"
        assert cases[0].source_analysis_id == analysis_id
        assert len(cases[0].analysis_version_hash) == 64
        assert len(cases[0].scenario_version_hash) == 64
        assert cases[0].definition["execution_kind"] == "workflow"
        assert cases[0].definition["steps"][0]["action"]["kind"] == "assert_visible"


def test_new_analysis_version_updates_case_deterministically(case_database) -> None:
    factory, (exploration_id, _page_id, _element_id, analysis_id) = case_database
    case_id = _sync(factory, exploration_id, analysis_id)[0]
    with factory() as session:
        original = session.get(TestCase, case_id)
        original_analysis_hash = original.analysis_version_hash
        original_scenario_hash = original.scenario_version_hash
    with factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        updated_result = dict(analysis.result)
        updated_result["potential_workflows"] = [dict(item) for item in analysis.result["potential_workflows"]]
        updated_result["potential_workflows"][0]["expected_outcome"] = "The verified button remains visible."
        analysis.result = updated_result
        session.commit()
    _sync(factory, exploration_id, analysis_id)

    with factory() as session:
        cases = list(session.scalars(select(TestCase).where(TestCase.exploration_id == exploration_id)))
        assert len(cases) == 1
        assert cases[0].id == case_id
        assert cases[0].is_current is True
        assert cases[0].definition["scenario"]["expected_outcome"] == "The verified button remains visible."
        assert cases[0].analysis_version_hash != original_analysis_hash
        assert cases[0].scenario_version_hash != original_scenario_hash


def test_incomplete_analysis_is_not_misrepresented_as_an_empty_case_list(case_database) -> None:
    factory, (exploration_id, _page_id, _element_id, analysis_id) = case_database
    with factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        analysis.status = "failed"
        analysis.result = None
        pages = list(session.scalars(select(ExplorationPage).where(ExplorationPage.exploration_id == exploration_id)))
        with pytest.raises(AnalysisNotReady, match="completed analysis"):
            sync_ai_test_cases(session, analysis, pages)
        assert session.scalar(select(TestCase.id).where(TestCase.exploration_id == exploration_id)) is None


def test_analysis_sync_never_removes_user_created_cases(case_database) -> None:
    factory, (exploration_id, _page_id, _element_id, analysis_id) = case_database
    ai_case_id = _sync(factory, exploration_id, analysis_id)[0]
    with factory() as session:
        analysis = session.get(ExplorationAnalysis, analysis_id)
        user_case = TestCase(
            id=uuid4(),
            exploration_id=exploration_id,
            source="user_created",
            identity_hash="u" * 64,
            source_analysis_id=None,
            analysis_version_hash=None,
            scenario_version_hash="v" * 64,
            definition={"schema_version": 1, "execution_kind": "workflow", "scenario": {}, "steps": []},
            is_current=True,
        )
        session.add(user_case)
        analysis.result["potential_workflows"] = []
        pages = list(session.scalars(select(ExplorationPage).where(ExplorationPage.exploration_id == exploration_id)))
        sync_ai_test_cases(session, analysis, pages)
        session.commit()
        saved_user_case = session.get(TestCase, user_case.id)
        saved_ai_case = session.get(TestCase, ai_case_id)

        assert saved_user_case is not None and saved_user_case.is_current is True
        assert saved_ai_case is not None and saved_ai_case.is_current is False
