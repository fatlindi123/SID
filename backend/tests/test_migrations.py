import os
import subprocess
import sys
from uuid import uuid4
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, insert, inspect, select
from sqlalchemy.engine import URL

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_migrations_create_exploration_storage_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-check.sqlite3"
    database_url = URL.create(
        "sqlite+pysqlite",
        database=str(database_path),
    ).render_as_string(hide_password=False)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    alembic_executable = Path(sys.executable).with_name(
        "alembic.exe" if os.name == "nt" else "alembic"
    )

    result = subprocess.run(
        [
            str(alembic_executable),
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    current = subprocess.run(
        [
            str(alembic_executable),
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "current",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert current.returncode == 0, current.stdout + current.stderr
    assert "0014_remove_google_identity" in current.stdout

    history = subprocess.run(
        [
            str(alembic_executable),
            "-c",
            str(BACKEND_ROOT / "alembic.ini"),
            "history",
        ],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert history.returncode == 0, history.stdout + history.stderr
    assert "0001_initial" in history.stdout
    assert "0002_explorations" in history.stdout
    assert "0003_exploration_analyses" in history.stdout
    assert "0004_test_execution" in history.stdout
    assert "0005_interaction_observations" in history.stdout
    assert "0006_persistent_test_cases" in history.stdout
    assert "0007_test_case_management" in history.stdout
    assert "0008_evidence_knowledge_graph" in history.stdout
    assert "0009_projects_management" in history.stdout
    assert "0010_workflow_intelligence" in history.stdout
    assert "0011_reports" in history.stdout
    assert "0012_authentication" in history.stdout
    assert "0013_first_login_onboarding" in history.stdout
    assert "0014_remove_google_identity" in history.stdout

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        test_run_columns = {column["name"] for column in inspector.get_columns("test_runs")}
        analysis_id_column = next(column for column in inspector.get_columns("test_runs") if column["name"] == "analysis_id")
        test_case_columns = {column["name"] for column in inspector.get_columns("test_cases")}
        foreign_keys = inspector.get_foreign_keys("test_runs")
        test_run_indexes = {index["name"] for index in inspector.get_indexes("test_runs")}
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        user_indexes = {index["name"] for index in inspector.get_indexes("users")}
    finally:
        engine.dispose()

    assert {
        "alembic_version",
        "projects",
        "project_metadata",
        "project_explorations",
        "explorations",
        "exploration_pages",
        "observed_elements",
        "exploration_events",
        "exploration_analyses",
        "test_runs",
        "test_run_steps",
        "interaction_observations",
        "test_cases",
        "knowledge_graph_nodes",
        "knowledge_graph_edges",
        "workflows",
        "workflow_steps",
        "reports",
        "users",
        "auth_sessions",
        "auth_challenges",
        "auth_bootstrap",
        "workspace_record_owners",
    } <= table_names
    assert "onboarding_completed_at" in user_columns
    assert "google_sub" not in user_columns
    assert "uq_users_google_sub" not in user_indexes
    assert "owner_id" not in {column["name"] for column in inspector.get_columns("explorations")}
    assert {"resource_type", "resource_id", "owner_id"} <= {
        column["name"] for column in inspector.get_columns("workspace_record_owners")
    }
    assert "test_case_id" in test_run_columns
    assert "ix_test_runs_test_case_id" in test_run_indexes
    assert analysis_id_column["nullable"] is True
    assert {"name", "description", "deleted_at"} <= test_case_columns
    assert any(
        foreign_key["constrained_columns"] == ["test_case_id"]
        and foreign_key["referred_table"] == "test_cases"
        for foreign_key in foreign_keys
    )


def test_management_migration_backfills_cases_and_preserves_historical_runs(tmp_path: Path) -> None:
    database_path = tmp_path / "management-backfill.sqlite3"
    database_url = URL.create("sqlite+pysqlite", database=str(database_path)).render_as_string(hide_password=False)
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    alembic_executable = Path(sys.executable).with_name("alembic.exe" if os.name == "nt" else "alembic")

    before = subprocess.run(
        [str(alembic_executable), "-c", str(BACKEND_ROOT / "alembic.ini"), "upgrade", "0006_persistent_test_cases"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert before.returncode == 0, before.stdout + before.stderr

    case_id = uuid4()
    exploration_id = uuid4()
    analysis_id = uuid4()
    run_id = uuid4()
    engine = create_engine(database_url)
    try:
        metadata = MetaData()
        test_cases = Table("test_cases", metadata, autoload_with=engine)
        test_runs = Table("test_runs", metadata, autoload_with=engine)
        with engine.begin() as connection:
            connection.execute(insert(test_cases).values(
                id=case_id.hex,
                exploration_id=exploration_id.hex,
                source="ai_generated",
                identity_hash="i" * 64,
                source_analysis_id=analysis_id.hex,
                analysis_version_hash="a" * 64,
                scenario_version_hash="s" * 64,
                definition={
                    "schema_version": 1,
                    "execution_kind": "workflow",
                    "scenario": {"title": "Preserved case title", "objective": "Preserved case objective."},
                    "steps": [],
                },
                is_current=True,
            ))
            connection.execute(insert(test_runs).values(
                id=run_id.hex,
                exploration_id=exploration_id.hex,
                analysis_id=analysis_id.hex,
                test_case_id=case_id.hex,
                scenario_index=-1,
                scenario={"title": "Immutable old run"},
                status="pending",
            ))
    finally:
        engine.dispose()

    after = subprocess.run(
        [str(alembic_executable), "-c", str(BACKEND_ROOT / "alembic.ini"), "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert after.returncode == 0, after.stdout + after.stderr

    engine = create_engine(database_url)
    try:
        metadata = MetaData()
        test_cases = Table("test_cases", metadata, autoload_with=engine)
        test_runs = Table("test_runs", metadata, autoload_with=engine)
        with engine.connect() as connection:
            case = connection.execute(select(test_cases).where(test_cases.c.id == case_id.hex)).mappings().one()
            run = connection.execute(select(test_runs).where(test_runs.c.id == run_id.hex)).mappings().one()
    finally:
        engine.dispose()

    assert case["name"] == "Preserved case title"
    assert case["description"] == "Preserved case objective."
    assert case["deleted_at"] is None
    assert case["id"] == case_id.hex
    assert run["id"] == run_id.hex
    assert run["analysis_id"] == analysis_id.hex
    assert run["scenario"] == {"title": "Immutable old run"}
