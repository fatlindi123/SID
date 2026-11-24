import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage, ObservedElement
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.schemas.analysis import (
    AnalysisEvidence,
    AnalysisResult,
    PageSummary,
    WebsitePurpose,
    WorkflowStep,
    WorkflowSuggestion,
)
from app.services.test_execution_worker import TestExecutionManager
from app.services.test_planner import compile_manual_login_smoke_test, compile_scenario
from app import models  # noqa: F401 - registers metadata


class StubBrowserEngine:
    def __init__(self, result: str = "passed") -> None:
        self.result = result
        self.calls = 0
        self.saved_steps: list[dict] = []

    async def execute_test_plan(
        self,
        *,
        start_url,
        saved_pages,
        steps,
        on_step_started,
        on_step_finished,
        is_cancelled,
    ):
        self.calls += 1
        self.saved_steps = steps
        for ordinal, _step in enumerate(steps):
            if is_cancelled():
                raise asyncio.CancelledError
            await on_step_started(ordinal)
            if self.result == "timeout":
                await on_step_finished(
                    ordinal,
                    "failed",
                    250,
                    "The browser test step timed out while waiting for the saved page or element.",
                    None,
                )
                return
            if self.result == "failed":
                await on_step_finished(
                    ordinal,
                    "failed",
                    12,
                    "The saved interface element is not visible.",
                    {"visible": False},
                )
                return
            await on_step_finished(
                ordinal,
                "passed",
                8,
                None,
                {"page_id": saved_pages[0]["id"], "visible": True},
            )


@pytest.fixture
def execution_database():
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
            pages_discovered=1,
        )
        page = ExplorationPage(
            exploration_id=exploration_id,
            url="https://shop.example/",
            title="Example Shop",
            depth=0,
            http_status=200,
            element_count=1,
        )
        session.add_all([exploration, page])
        session.flush()
        element = ObservedElement(
            page_id=page.id,
            ordinal=0,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Continue",
            selector="button#continue",
            attributes={"id": "continue", "type": "button"},
        )
        session.add(element)
        session.flush()
        page.elements.append(element)
        workflow = WorkflowSuggestion(
            title="Verify the continue button",
            objective="Check the saved primary action is displayed.",
            relevant_page_ids=[page.id],
            steps=[
                WorkflowStep(
                    instruction="Verify the Continue button is visible.",
                    evidence=[AnalysisEvidence(page_id=page.id, element_ids=[element.id])],
                )
            ],
            expected_outcome="The Continue button is visible on the saved page.",
        )
        result = AnalysisResult(
            website_purpose=WebsitePurpose(
                interpretation="The page presents a primary action.",
                evidence=[AnalysisEvidence(page_id=page.id, element_ids=[element.id])],
            ),
            page_summaries=[
                PageSummary(
                    page_id=page.id,
                    interpretation="The saved page contains a Continue button.",
                    evidence=[AnalysisEvidence(page_id=page.id, element_ids=[element.id])],
                )
            ],
            main_navigation=[],
            important_interactive_elements=[],
            forms_and_fields=[],
            potential_workflows=[workflow],
        )
        analysis = ExplorationAnalysis(
            exploration_id=exploration_id,
            status="completed",
            attempt_count=1,
            result=result.model_dump(mode="json"),
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(analysis)
        session.flush()
        compiled = compile_scenario(0, workflow, [page])
        run = TestRun(
            exploration_id=exploration_id,
            analysis_id=analysis.id,
            scenario_index=0,
            scenario={**compiled.scenario, "scenario_index": 0},
            status="pending",
        )
        session.add(run)
        session.flush()
        for planned in compiled.steps:
            session.add(
                TestRunStep(
                    test_run_id=run.id,
                    ordinal=planned.ordinal,
                    instruction=planned.instruction,
                    action=planned.action,
                    expected_evidence=planned.expected_evidence,
                    status="pending",
                )
            )
        session.commit()
        run_id = run.id
        page_id = page.id
    try:
        yield factory, exploration_id, run_id, page_id
    finally:
        engine.dispose()


def _run_manager(factory, run_id, browser_result):
    engine = StubBrowserEngine(browser_result)
    manager = TestExecutionManager(
        factory,
        Settings(_env_file=None),
        engine_factory=lambda: engine,
    )

    async def run():
        manager.enqueue(run_id)
        await asyncio.wait_for(asyncio.shield(manager._tasks[run_id]), timeout=3)
        await manager.stop()

    asyncio.run(run())
    return engine


@pytest.mark.parametrize("browser_result", ["passed", "failed", "timeout"])
def test_worker_persists_real_step_outcome_and_never_marks_failure_passed(
    execution_database, browser_result
) -> None:
    factory, _exploration_id, run_id, page_id = execution_database

    engine = _run_manager(factory, run_id, browser_result)

    with factory() as session:
        run = session.scalar(
            select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
        )
        assert run is not None
        step = run.steps[0]
        assert engine.calls == 1
        assert step.duration_ms is not None
        if browser_result == "passed":
            assert run.status == "passed"
            assert step.status == "passed"
            assert step.actual_evidence["page_id"] == str(page_id)
        else:
            assert run.status == "failed"
            assert step.status == "failed"
            assert step.status != "passed"
            assert step.error_message
            if browser_result == "timeout":
                assert "timed out" in step.error_message


def test_queued_run_executes_its_snapshot_after_case_and_analysis_change(execution_database) -> None:
    factory, exploration_id, run_id, _page_id = execution_database
    case_id = uuid4()
    with factory() as session:
        run = session.scalar(
            select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
        )
        analysis = session.scalar(select(ExplorationAnalysis).where(ExplorationAnalysis.exploration_id == exploration_id))
        assert run is not None and analysis is not None
        scenario = {key: value for key, value in run.scenario.items() if key != "scenario_index"}
        saved_steps = [
            {
                "ordinal": step.ordinal,
                "instruction": step.instruction,
                "action": step.action,
                "expected_evidence": step.expected_evidence,
            }
            for step in run.steps
        ]
        definition = {
            "schema_version": 1,
            "execution_kind": "workflow",
            "scenario": scenario,
            "steps": saved_steps,
        }
        test_case = TestCase(
            id=case_id,
            exploration_id=exploration_id,
            source="ai_generated",
            identity_hash="i" * 64,
            source_analysis_id=analysis.id,
            analysis_version_hash="a" * 64,
            scenario_version_hash="s" * 64,
            definition=definition,
            is_current=True,
        )
        session.add(test_case)
        session.flush()
        run.test_case_id = case_id
        run.scenario = {
            **definition,
            "source": "ai_generated",
            "test_case_id": str(case_id),
            "source_analysis_id": str(analysis.id),
            "analysis_version_hash": "a" * 64,
            "scenario_version_hash": "s" * 64,
        }
        # This models a reanalysis and case refresh after the run was queued.
        analysis.status = "failed"
        analysis.result = None
        test_case.definition = {
            "schema_version": 1,
            "execution_kind": "workflow",
            "scenario": {"title": "Changed after queueing"},
            "steps": [],
        }
        session.commit()

    engine = _run_manager(factory, run_id, "passed")

    with factory() as session:
        run = session.get(TestRun, run_id)
        assert run is not None
        assert run.status == "passed"
        assert engine.calls == 1
        assert engine.saved_steps[0]["action"]["selector"] == "button#continue"
        assert run.scenario["scenario"]["title"] == "Verify the continue button"


def test_worker_revalidates_and_persists_manual_saucedemo_smoke_test(execution_database) -> None:
    factory, exploration_id, _existing_run_id, page_id = execution_database
    with factory() as session:
        exploration = session.get(Exploration, exploration_id)
        analysis = session.scalar(
            select(ExplorationAnalysis).where(ExplorationAnalysis.exploration_id == exploration_id)
        )
        page = session.scalar(
            select(ExplorationPage).options(selectinload(ExplorationPage.elements)).where(ExplorationPage.id == page_id)
        )
        assert exploration is not None and analysis is not None and page is not None
        exploration.start_url = "https://www.saucedemo.com/"
        page.url = exploration.start_url
        login = ObservedElement(
            page_id=page.id,
            ordinal=1,
            kind="input",
            tag_name="input",
            role="button",
            accessible_name="Login",
            selector="input#login-button",
            attributes={"id": "login-button", "type": "submit"},
        )
        session.add(login)
        session.flush()
        page.elements.append(login)
        compiled = compile_manual_login_smoke_test(page.id, login.id, [page], exploration.start_url)
        manual_run = TestRun(
            exploration_id=exploration_id,
            analysis_id=analysis.id,
            scenario_index=-1,
            scenario={**compiled.scenario, "scenario_index": -1},
            status="pending",
        )
        session.add(manual_run)
        session.flush()
        for planned in compiled.steps:
            session.add(
                TestRunStep(
                    test_run_id=manual_run.id,
                    ordinal=planned.ordinal,
                    instruction=planned.instruction,
                    action=planned.action,
                    expected_evidence=planned.expected_evidence,
                    status="pending",
                )
            )
        manual_run_id = manual_run.id
        login_id = login.id
        session.commit()

    engine = _run_manager(factory, manual_run_id, "passed")

    with factory() as session:
        run = session.scalar(
            select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == manual_run_id)
        )
        assert run is not None
        assert engine.calls == 1
        assert run.status == "passed"
        assert run.scenario["source"] == "manual"
        assert [step.status for step in run.steps] == ["passed", "passed"]
        assert run.steps[0].action["kind"] == "navigate"
        assert run.steps[1].action["kind"] == "assert_visible"
        assert run.steps[1].action["element_id"] == str(login_id)


def test_recovery_fails_interrupted_run_without_replaying_browser_actions(execution_database) -> None:
    factory, _exploration_id, run_id, _page_id = execution_database
    with factory() as session:
        run = session.get(TestRun, run_id)
        step = session.scalar(select(TestRunStep).where(TestRunStep.test_run_id == run_id))
        run.status = "running"
        step.status = "running"
        session.commit()

    engine = StubBrowserEngine()
    manager = TestExecutionManager(factory, Settings(_env_file=None), engine_factory=lambda: engine)
    asyncio.run(manager.start())

    with factory() as session:
        run = session.get(TestRun, run_id)
        step = session.scalar(select(TestRunStep).where(TestRunStep.test_run_id == run_id))
        assert run.status == "failed"
        assert step.status == "failed"
        assert "not replayed" in run.error_message
    assert engine.calls == 0


def test_running_test_can_be_cancelled_and_pending_step_is_not_reported_as_passed(execution_database) -> None:
    factory, _exploration_id, run_id, _page_id = execution_database

    class BlockingEngine(StubBrowserEngine):
        async def execute_test_plan(
            self,
            *,
            start_url,
            saved_pages,
            steps,
            on_step_started,
            on_step_finished,
            is_cancelled,
        ):
            await on_step_started(0)
            while not is_cancelled():
                await asyncio.sleep(0.005)
            raise asyncio.CancelledError

    engine = BlockingEngine()
    manager = TestExecutionManager(
        factory,
        Settings(_env_file=None),
        engine_factory=lambda: engine,
    )

    async def cancel_running_run():
        manager.enqueue(run_id)
        for _ in range(100):
            await asyncio.sleep(0.01)
            with factory() as session:
                run = session.get(TestRun, run_id)
                if run is not None and run.status == "running":
                    break
        assert run.status == "running"
        assert await manager.cancel(run_id) is True
        await manager.stop()

    asyncio.run(cancel_running_run())

    with factory() as session:
        run = session.get(TestRun, run_id)
        step = session.scalar(select(TestRunStep).where(TestRunStep.test_run_id == run_id))
        assert run.status == "cancelled"
        assert step.status == "cancelled"
        assert step.status != "passed"
