from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.models.exploration import (
    Exploration,
    ExplorationAnalysis,
    ExplorationEvent,
    ExplorationPage,
    ObservedElement,
)
from app.models.interaction_observation import InteractionObservation
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.project import Project, ProjectExploration, ProjectMetadata
from app.models.report import GeneratedReport
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.models.workflow import Workflow, WorkflowStep


def _seed_report_sources(client, *, project_id=None, suffix="one", status="completed"):
    exploration_id = uuid4()
    page_id = uuid4()
    target_page_id = uuid4()
    element_id = uuid4()
    form_id = uuid4()
    field_id = uuid4()
    analysis_id = uuid4()
    observation_id = uuid4()
    page_node_id = uuid4()
    target_node_id = uuid4()
    element_node_id = uuid4()
    graph_edge_id = uuid4()
    workflow_id = uuid4()
    workflow_step_id = uuid4()
    case_id = uuid4()
    run_id = uuid4()
    run_step_id = uuid4()
    now = datetime.now(UTC)
    start_url = f"https://reports-fixture.example/{suffix}?token=must-not-appear"
    with client.app.state.test_session_factory() as session:
        if project_id is not None and session.get(Project, project_id) is None:
            session.add(Project(id=project_id, name="Reports project", target_url="https://reports-fixture.example/"))
            session.add(ProjectMetadata(project_id=project_id, description="Fixture metadata", environment="test"))
            session.flush()
        exploration = Exploration(
            id=exploration_id, start_url=start_url, current_url=start_url,
            status=status, max_pages=20, max_depth=3,
            pages_discovered=2, elements_discovered=4, event_count=1,
            created_at=now - timedelta(minutes=5), started_at=now - timedelta(minutes=4),
            completed_at=now - timedelta(minutes=1) if status == "completed" else None,
            error_message="password=internal-secret" if status == "failed" else None,
        )
        session.add(exploration)
        page = ExplorationPage(
            id=page_id, exploration_id=exploration_id, url=start_url,
            title="Fixture page", description="description not included", depth=0,
            http_status=200, visible_text="page text with secret page-value", dom_outline=[],
            screenshot=b"should not be loaded", screenshot_mime="image/png", element_count=3,
            discovered_at=now - timedelta(minutes=3),
        )
        target_page = ExplorationPage(
            id=target_page_id, exploration_id=exploration_id,
            url=f"https://reports-fixture.example/{suffix}/next", title="Next page",
            depth=1, http_status=200, visible_text="", dom_outline=[], element_count=0,
            discovered_at=now - timedelta(minutes=2),
        )
        button = ObservedElement(
            id=element_id, page_id=page_id, ordinal=0, kind="button", tag_name="button",
            role="button", accessible_name="Continue", label=None, text="Continue",
            selector="button", attributes={"value": "must-not-appear", "type": "button"},
        )
        form = ObservedElement(
            id=form_id, page_id=page_id, ordinal=1, kind="form", tag_name="form",
            role=None, accessible_name="Sign in", label="Sign in", text=None, selector="form", attributes={},
        )
        field = ObservedElement(
            id=field_id, page_id=page_id, ordinal=2, kind="input", tag_name="input",
            role="textbox", accessible_name="Password", label="Password", text=None,
            selector="input[type=password]", attributes={"type": "password", "value": "must-not-appear"},
        )
        session.add_all([page, target_page, button, form, field])
        analysis_result = {
            "website_purpose": {"interpretation": "A sample page with password:analysis-secret", "evidence": [{"page_id": str(page_id), "element_ids": [str(element_id)]}]},
            "page_summaries": [{"page_id": str(page_id), "interpretation": "A page summary", "evidence": [{"page_id": str(page_id), "element_ids": [str(element_id)]}]}],
            "main_navigation": [{"evidence": {"page_id": str(page_id), "element_ids": [str(element_id)]}, "target_page_id": str(target_page_id), "interpretation": "Continue to the next saved page"}],
            "important_interactive_elements": [{"evidence": {"page_id": str(page_id), "element_ids": [str(element_id)]}, "interpretation": "Continue button"}],
            "forms_and_fields": [{"page_id": str(page_id), "form_element_ids": [str(form_id)], "field_element_ids": [str(field_id)], "relationship_note": "page_level_only"}],
            "potential_workflows": [{"title": "Suggested path", "objective": "Review the page", "relevant_page_ids": [str(page_id)], "steps": [{"instruction": "Inspect the saved page", "evidence": [{"page_id": str(page_id), "element_ids": [str(element_id)]}]}], "expected_outcome": "A page is shown"}],
            "warnings": [{"collection": "potential_workflows", "discarded_count": 1}],
        }
        analysis = ExplorationAnalysis(
            id=analysis_id, exploration_id=exploration_id, status="completed", attempt_count=1,
            model="test-model", result=analysis_result, requested_at=now - timedelta(minutes=2),
            completed_at=now - timedelta(minutes=1), updated_at=now,
        )
        session.add(analysis)
        session.add(ExplorationEvent(
            id=uuid4(), exploration_id=exploration_id, sequence=1, event_type="page_visited",
            message="Authorization: Bearer must-not-appear", url=start_url,
            details={"cookie": "must-not-appear"}, created_at=now,
        ))
        session.add(InteractionObservation(
            id=observation_id, exploration_id=exploration_id, page_id=page_id,
            element_ordinal=0, probe_key="probe", action_type="disclosure", outcome="observed",
            reason=None,
            before_evidence={"url": start_url, "title": "Fixture", "target": {"tag_name": "button", "role": "button", "name": "Continue", "expanded": False, "selected": None}, "visible_changes": {"added": [], "removed": [], "state_changes": []}},
            after_evidence={"url": start_url, "title": "Fixture", "target": {"tag_name": "button", "role": "button", "name": "Continue", "expanded": True, "selected": None}, "visible_changes": {"added": [{"name": "secret"}], "removed": [], "state_changes": []}},
            duration_ms=12, created_at=now,
        ))
        session.add_all([
            KnowledgeGraphNode(
                id=page_node_id, exploration_id=exploration_id, node_type="page",
                source_key=str(page_id), label="Fixture page", page_id=page_id,
                provenance="verified", evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}], properties={"sensitive": "not loaded"},
            ),
            KnowledgeGraphNode(
                id=target_node_id, exploration_id=exploration_id, node_type="page",
                source_key=str(target_page_id), label="Next page", page_id=target_page_id,
                provenance="verified", evidence=[{"source_type": "page", "source_id": str(target_page_id), "page_id": str(target_page_id)}], properties={},
            ),
            KnowledgeGraphNode(
                id=element_node_id, exploration_id=exploration_id, node_type="ui_element",
                source_key=str(element_id), label="Continue", page_id=page_id,
                provenance="verified", evidence=[{"source_type": "element", "source_id": str(element_id), "page_id": str(page_id), "element_id": str(element_id)}], properties={},
            ),
            KnowledgeGraphEdge(
                id=graph_edge_id, exploration_id=exploration_id,
                source_node_id=page_node_id, target_node_id=target_node_id,
                edge_type="links_to", provenance="verified",
                evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}, {"source_type": "element", "source_id": str(element_id), "page_id": str(page_id), "element_id": str(element_id)}, {"source_type": "page", "source_id": str(target_page_id), "page_id": str(target_page_id)}], properties={},
            ),
        ])
        session.add(Workflow(
            id=workflow_id, exploration_id=exploration_id, project_id=project_id,
            identity_hash="a" * 64, source_type="page_link", source_id=str(graph_edge_id),
            source_hash="b" * 64, provenance="observed", status="active",
            name="Observed link path", description="Persisted workflow", evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}],
        ))
        session.add(WorkflowStep(
            id=workflow_step_id, workflow_id=workflow_id, ordinal=0,
            step_type="navigate", provenance="observed", instruction="Open the saved next page",
            page_id=page_id, target_page_id=target_page_id, page_url=start_url,
            page_title="Fixture page", control_name="Continue",
            evidence=[{"source_type": "page", "source_id": str(page_id), "page_id": str(page_id)}],
            observed_state={"expanded": False}, expected_state={"selected": True},
        ))
        session.add(TestCase(
            id=case_id, exploration_id=exploration_id, source="ai_generated",
            identity_hash="c" * 64, source_analysis_id=analysis_id,
            analysis_version_hash="d" * 64, scenario_version_hash="e" * 64,
            name="Saved case", description="Case description", definition={"scenario": {"password": "must-not-appear"}},
            is_current=True, created_at=now - timedelta(minutes=1), updated_at=now,
        ))
        session.add(TestRun(
            id=run_id, exploration_id=exploration_id, analysis_id=analysis_id,
            scenario_index=0, test_case_id=case_id,
            scenario={"password": "must-not-appear"}, status="failed",
            error_message="password=run-secret", created_at=now, started_at=now,
            completed_at=now,
        ))
        session.add(TestRunStep(
            id=run_step_id, test_run_id=run_id, ordinal=0, instruction="Submit password=run-secret",
            action={"value": "must-not-appear"}, expected_evidence={"token": "must-not-appear"},
            status="failed", duration_ms=9, error_message="password=step-secret",
            actual_evidence={"cookie": "must-not-appear"}, started_at=now, completed_at=now,
        ))
        if project_id is not None:
            session.add(ProjectExploration(project_id=project_id, exploration_id=exploration_id))
        session.commit()
    return {
        "exploration_id": exploration_id, "page_id": page_id, "target_page_id": target_page_id,
        "element_id": element_id, "form_id": form_id, "field_id": field_id,
        "analysis_id": analysis_id, "observation_id": observation_id,
        "page_node_id": page_node_id, "target_node_id": target_node_id,
        "workflow_id": workflow_id, "workflow_step_id": workflow_step_id,
        "case_id": case_id, "run_id": run_id, "run_step_id": run_step_id,
    }


def _create_project(client, *, archived=False):
    project_id = uuid4()
    with client.app.state.test_session_factory() as session:
        session.add(Project(id=project_id, name="Reports project", target_url="https://reports-fixture.example/"))
        session.add(ProjectMetadata(
            project_id=project_id, description="Project report", environment="staging",
            archived_at=datetime.now(UTC) if archived else None,
        ))
        session.commit()
    return project_id


def test_exploration_report_uses_saved_sources_and_redacts_sensitive_payloads(client):
    source = _seed_report_sources(client)
    response = client.post(f"/api/v1/reports/explorations/{source['exploration_id']}", json={})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["status"] == "completed"
    assert saved["project_id"] is None
    snapshot = saved["snapshot"]
    assert snapshot["source_record_ids"]["pages"] == [str(source["page_id"]), str(source["target_page_id"])]
    assert snapshot["summary"]["persisted_page_rows"] == 2
    assert snapshot["summary"]["persisted_element_rows"] == 3
    assert snapshot["summary"]["test_case_latest_execution_counts"]["failed"] == 1
    assert snapshot["summary"]["test_case_latest_execution_counts"]["never_executed"] == 0
    exploration = snapshot["sections"]["explorations"][0]
    assert exploration["pages_and_navigation"]["verified_navigation_links"][0]["provenance"] == "verified"
    assert exploration["interaction_observations"]["items"][0]["outcome"] == "observed"
    assert exploration["ai_analysis"]["result"]["provenance"] == "ai_interpretation"
    assert exploration["ai_analysis"]["result"]["potential_workflows"][0]["provenance"] == "suggested"
    assert exploration["ai_analysis"]["result"]["potential_workflows"][0]["executed"] is False
    assert exploration["ai_analysis"]["result"]["warnings"] == [
        {"collection": "potential_workflows", "discarded_count": 1}
    ]
    assert exploration["workflows"]["observed"][0]["id"] == str(source["workflow_id"])
    assert exploration["workflows"]["suggested"] == []
    assert exploration["test_runs"]["failed_count"] == 1
    assert exploration["test_runs"]["items"][0]["steps"][0]["failure_summary"] == "Execution failed; internal details are omitted"
    assert "form_parent_relationships_persisted" in exploration["ui_elements_and_forms"]
    assert "No persisted" not in " ".join(snapshot["sections"]["known_limitations"])
    flattened = str(snapshot)
    for sensitive in ("must-not-appear", "analysis-secret", "run-secret", "step-secret", "internal-secret"):
        assert sensitive not in flattened
    assert "token=must-not-appear" not in flattened
    assert "[redacted]" in flattened


def test_project_report_includes_only_explicitly_assigned_explorations_and_allows_archived_read(client):
    project_id = _create_project(client, archived=True)
    first = _seed_report_sources(client, project_id=project_id, suffix="assigned-a")
    second = _seed_report_sources(client, project_id=project_id, suffix="assigned-b")
    unassigned = _seed_report_sources(client, suffix="unassigned")
    response = client.post(f"/api/v1/reports/projects/{project_id}", json={})
    assert response.status_code == 201, response.text
    snapshot = response.json()["snapshot"]
    assert snapshot["scope"]["archived_project_included"] is True
    assert {item["id"] for item in snapshot["sections"]["exploration_metadata"]} == {
        str(first["exploration_id"]), str(second["exploration_id"]),
    }
    assert str(unassigned["exploration_id"]) not in snapshot["source_record_ids"]["explorations"]
    assert len(snapshot["sections"]["explorations"]) == 2


def test_reports_mark_missing_sources_and_do_not_call_failed_exploration_success(client):
    project_id, exploration_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    with client.app.state.test_session_factory() as session:
        session.add(Project(id=project_id, name="Metadata incomplete project", target_url="https://missing.example/"))
        session.add(Exploration(
            id=exploration_id,
            start_url="https://missing.example/",
            status="failed",
            max_pages=4,
            max_depth=1,
            pages_discovered=0,
            elements_discovered=0,
            event_count=0,
            error_message="connection failed",
            created_at=now,
            started_at=now,
            completed_at=now,
        ))
        session.add(ProjectExploration(project_id=project_id, exploration_id=exploration_id))
        session.commit()

    response = client.post(f"/api/v1/reports/projects/{project_id}", json={})
    assert response.status_code == 201, response.text
    snapshot = response.json()["snapshot"]
    project = snapshot["sections"]["project_metadata"]
    assert project["metadata_status"] == "partial"
    assert project["description"] is None and project["environment"] is None
    exploration = snapshot["sections"]["explorations"][0]
    assert snapshot["sections"]["exploration_metadata"][0]["status"] == "failed"
    assert exploration["pages_and_navigation"]["status"] == "empty"
    assert exploration["ai_analysis"]["status"] == "not_started"
    assert exploration["knowledge_graph"]["status"] == "not_built"
    assert snapshot["summary"]["persisted_page_rows"] == 0
    assert any("no persisted page records" in message for message in snapshot["sections"]["known_limitations"])


def test_stale_evidence_is_marked_and_excluded_from_verified_navigation(client):
    source = _seed_report_sources(client)
    with client.app.state.test_session_factory() as session:
        session.delete(session.get(ObservedElement, source["element_id"]))
        session.commit()

    response = client.post(f"/api/v1/reports/explorations/{source['exploration_id']}", json={})
    assert response.status_code == 201, response.text
    snapshot = response.json()["snapshot"]
    exploration = snapshot["sections"]["explorations"][0]
    nodes = exploration["knowledge_graph"]["verified_nodes"]
    element_node = next(node for node in nodes if node["node_type"] == "ui_element")
    edge = exploration["knowledge_graph"]["verified_relationships"][0]
    assert element_node["evidence_status"] == "partial"
    assert edge["evidence_status"] == "partial"
    assert exploration["pages_and_navigation"]["verified_navigation_links"] == []
    assert exploration["ai_analysis"]["result"]["important_interactive_elements"] == []
    assert any("stale page/element evidence" in message for message in snapshot["sections"]["known_limitations"])


def test_reports_are_immutable_and_each_generation_creates_a_new_record(client):
    source = _seed_report_sources(client)
    path = f"/api/v1/reports/explorations/{source['exploration_id']}"
    first = client.post(path, json={}).json()
    second = client.post(path, json={}).json()
    assert first["id"] != second["id"]
    first_content = dict(first["snapshot"])
    second_content = dict(second["snapshot"])
    first_content.pop("generated_at")
    second_content.pop("generated_at")
    assert first_content == second_content
    with client.app.state.test_session_factory() as session:
        page = session.get(ExplorationPage, source["page_id"])
        page.title = "Changed after generation"
        session.commit()
    saved_first = client.get(f"/api/v1/reports/{first['id']}").json()
    third = client.post(path, json={}).json()
    assert saved_first["snapshot"] == first["snapshot"]
    first_report_pages = saved_first["snapshot"]["sections"]["explorations"][0]["pages_and_navigation"]["pages"]
    third_report_pages = third["snapshot"]["sections"]["explorations"][0]["pages_and_navigation"]["pages"]
    assert first_report_pages[0]["title"] == "Fixture page"
    assert third_report_pages[0]["title"] == "Changed after generation"
    with client.app.state.test_session_factory() as session:
        assert len(list(session.scalars(select(GeneratedReport)))) == 3


def test_report_history_filters_pagination_and_scope_validation(client):
    project_id = _create_project(client)
    first = _seed_report_sources(client, project_id=project_id, suffix="history-a")
    second = _seed_report_sources(client, suffix="history-b")
    client.post(f"/api/v1/reports/projects/{project_id}", json={})
    client.post(f"/api/v1/reports/explorations/{first['exploration_id']}", json={})
    client.post(f"/api/v1/reports/explorations/{second['exploration_id']}", json={})
    by_project = client.get("/api/v1/reports", params={"project_id": str(project_id), "limit": 1, "offset": 0})
    assert by_project.status_code == 200
    assert by_project.json()["total"] == 2
    assert len(by_project.json()["items"]) == 1
    assert "snapshot" not in by_project.json()["items"][0]
    assert "generated_at" in by_project.json()["items"][0]
    by_type = client.get("/api/v1/reports", params={"report_type": "exploration", "exploration_id": str(second["exploration_id"])})
    assert by_type.status_code == 200 and by_type.json()["total"] == 1
    mismatch = client.get("/api/v1/reports", params={"project_id": str(project_id), "exploration_id": str(second["exploration_id"])})
    assert mismatch.status_code == 422
    missing = client.post(f"/api/v1/reports/projects/{uuid4()}", json={})
    assert missing.status_code == 404
    invalid_range = client.post(f"/api/v1/reports/explorations/{second['exploration_id']}", json={
        "run_created_from": "2026-11-23T13:00:00Z", "run_created_to": "2026-11-23T12:00:00Z",
    })
    assert invalid_range.status_code == 422
    naive_date = client.get("/api/v1/reports", params={"generated_from": "2026-11-23T12:00:00"})
    assert naive_date.status_code == 422


def test_saved_json_print_and_pdf_exports_use_the_same_snapshot(client):
    project_id = _create_project(client)
    source = _seed_report_sources(client, project_id=project_id)
    generated = client.post(f"/api/v1/reports/explorations/{source['exploration_id']}", json={}).json()
    report_id = generated["id"]
    detail = client.get(f"/api/v1/reports/{report_id}").json()
    json_export = client.get(f"/api/v1/reports/{report_id}/export.json")
    print_view = client.get(f"/api/v1/reports/{report_id}/print")
    assert json_export.status_code == 200
    assert json_export.json() == detail
    assert print_view.status_code == 200
    assert "software intelligence" in print_view.text.casefold()
    assert "Fixture page" in print_view.text
    assert "must-not-appear" not in print_view.text
    pdf_export = client.get(f"/api/v1/reports/{report_id}/export.pdf")
    assert pdf_export.status_code == 200, pdf_export.text[:300]
    assert pdf_export.headers["content-type"] == "application/pdf"
    assert pdf_export.content.startswith(b"%PDF-")
    assert pdf_export.content.rstrip().endswith(b"%%EOF")
    assert len(pdf_export.content) > 1000


def test_report_metrics_classify_each_test_case_and_run_status(client):
    source = _seed_report_sources(client)
    now = datetime.now(UTC)
    statuses = ("pending", "running", "passed", "cancelled")
    with client.app.state.test_session_factory() as session:
        for status in statuses:
            case_id = uuid4()
            hash_value = uuid4().hex + uuid4().hex
            session.add(TestCase(
                id=case_id,
                exploration_id=source["exploration_id"],
                source="user_created",
                identity_hash=hash_value,
                source_analysis_id=None,
                analysis_version_hash=None,
                scenario_version_hash=hash_value,
                name=f"{status} case",
                description="Status fixture",
                definition={"scenario": {"steps": []}},
                is_current=True,
                created_at=now,
                updated_at=now,
            ))
            session.add(TestRun(
                id=uuid4(),
                exploration_id=source["exploration_id"],
                analysis_id=None,
                scenario_index=0,
                test_case_id=case_id,
                scenario={"steps": []},
                status=status,
                created_at=now,
                started_at=now if status != "pending" else None,
                completed_at=now if status in {"passed", "cancelled"} else None,
            ))
        never_id = uuid4()
        hash_value = uuid4().hex + uuid4().hex
        session.add(TestCase(
            id=never_id,
            exploration_id=source["exploration_id"],
            source="user_created",
            identity_hash=hash_value,
            source_analysis_id=None,
            analysis_version_hash=None,
            scenario_version_hash=hash_value,
            name="Never executed case",
            description="Status fixture",
            definition={"scenario": {"steps": []}},
            is_current=True,
            created_at=now,
            updated_at=now,
        ))
        session.commit()

    response = client.post(f"/api/v1/reports/explorations/{source['exploration_id']}", json={})
    assert response.status_code == 201, response.text
    summary = response.json()["snapshot"]["summary"]
    assert summary["test_case_latest_execution_counts"] == {
        "cancelled": 1,
        "failed": 1,
        "never_executed": 1,
        "passed": 1,
        "pending": 1,
        "running": 1,
    }
    assert summary["test_run_status_counts"] == {
        "cancelled": 1,
        "failed": 1,
        "passed": 1,
        "pending": 1,
        "running": 1,
    }
    assert "latest linked TestRun" in summary["metric_definitions"]["test_case_latest_execution_counts"]


def test_failed_generation_is_saved_with_sanitized_error_and_no_snapshot(client, monkeypatch):
    source = _seed_report_sources(client)
    from app.services import reports as report_service

    def fail(*_args, **_kwargs):
        raise RuntimeError("password=do-not-leak")

    monkeypatch.setattr(report_service, "_make_snapshot", fail)
    response = client.post(f"/api/v1/reports/explorations/{source['exploration_id']}", json={})
    assert response.status_code == 500
    assert "do-not-leak" not in response.text
    history = client.get("/api/v1/reports", params={"exploration_id": str(source["exploration_id"])})
    failed = history.json()["items"][0]
    assert failed["status"] == "failed"
    assert failed["error_code"] == "report_generation_failed"
    assert "snapshot" not in failed
    failed_detail = client.get(f"/api/v1/reports/{failed['id']}")
    assert failed_detail.status_code == 200
    assert failed_detail.json()["snapshot"] is None
    assert client.get(f"/api/v1/reports/{failed['id']}/export.json").status_code == 409
