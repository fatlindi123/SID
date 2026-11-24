import asyncio
import json
from collections.abc import Generator
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.db.base import Base
from app.models.exploration import (
    Exploration,
    ExplorationAnalysis,
    ExplorationPage,
    ObservedElement,
)
from app.models.test_case import TestCase
from app.schemas.analysis_draft import AnalysisDraft
from app.services.ai_provider import AIProviderError, OpenAICompatibleProvider
from app.services.analysis_service import AIAnalysisService, _analysis_response_schema
from app.services.analysis_worker import AnalysisManager
from app import models  # noqa: F401 - registers metadata


class StubProvider:
    def __init__(
        self,
        response: str | Exception,
        response_model: str | None = None,
        response_diagnostics: dict | None = None,
    ) -> None:
        self.response = response
        self.response_model = response_model
        self.response_diagnostics = response_diagnostics or {}
        self.user_payload: dict | None = None
        self.response_schema: dict | None = None
        self.complete_calls = 0

    async def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict | None = None,
    ) -> str:
        self.complete_calls += 1
        self.user_payload = user_payload
        self.response_schema = response_schema
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def analysis_database() -> Generator[tuple[sessionmaker[Session], object, object], None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    exploration_id = uuid4()
    with session_factory() as session:
        exploration = Exploration(
            id=exploration_id,
            start_url="https://shop.example/",
            status="completed",
            max_pages=20,
            max_depth=3,
            pages_discovered=2,
        )
        root = ExplorationPage(
            exploration_id=exploration_id,
            url="https://shop.example/",
            title="Example Shop",
            depth=0,
            http_status=200,
            visible_text="Products and search",
            dom_outline=[{"tag_name": "h1", "role": "heading", "name": "Products"}],
            element_count=4,
        )
        catalog = ExplorationPage(
            exploration_id=exploration_id,
            url="https://shop.example/catalog",
            title="Catalog",
            depth=1,
            http_status=200,
            visible_text="Catalog listings",
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
            attributes={"href": "https://shop.example/catalog", "inside_form": False},
        )
        form = ObservedElement(
            page_id=root.id,
            ordinal=1,
            kind="form",
            tag_name="form",
            role="form",
            accessible_name="Search products",
            selector="form#search",
            attributes={},
        )
        field = ObservedElement(
            page_id=root.id,
            ordinal=2,
            kind="input",
            tag_name="input",
            role="searchbox",
            accessible_name="Search products",
            label="Search products",
            selector="input#query",
            attributes={"type": "search", "name": "query"},
        )
        search_button = ObservedElement(
            page_id=root.id,
            ordinal=3,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Search",
            selector="button#search",
            attributes={},
        )
        sort_button = ObservedElement(
            page_id=catalog.id,
            ordinal=0,
            kind="button",
            tag_name="button",
            role="button",
            accessible_name="Sort products",
            selector="button#sort",
            attributes={},
        )
        session.add_all([root_link, form, field, search_button, sort_button])
        session.commit()
        ids = {
            "root_page": root.id,
            "catalog_page": catalog.id,
            "root_link": root_link.id,
            "form": form.id,
            "field": field.id,
            "search_button": search_button.id,
            "sort_button": sort_button.id,
        }
    try:
        yield session_factory, exploration_id, ids
    finally:
        engine.dispose()


def _valid_draft(ids: dict) -> str:
    draft = AnalysisDraft(
        website_purpose={
            "interpretation": "The saved interface appears to help users find products.",
            "evidence": [{"page_id": ids["root_page"], "element_ids": [ids["root_link"]]}],
        },
        page_summaries=[
            {
                "page_id": ids["root_page"],
                "interpretation": "The landing page exposes product search and a catalog link.",
                "evidence": [{"page_id": ids["root_page"], "element_ids": [ids["root_link"]]}],
            },
            {
                "page_id": ids["catalog_page"],
                "interpretation": "The catalog page includes a product sorting control.",
                "evidence": [{"page_id": ids["catalog_page"], "element_ids": [ids["sort_button"]]}],
            },
        ],
        main_navigation=[
            {
                "evidence": {"page_id": ids["root_page"], "element_ids": [ids["root_link"]]},
                "target_page_id": ids["catalog_page"],
                "interpretation": "This link leads to the saved product catalog.",
            }
        ],
        important_interactive_elements=[
            {
                "evidence": {"page_id": ids["root_page"], "element_ids": [ids["field"]]},
                "interpretation": "This field supports product search.",
            }
        ],
        potential_workflows=[
            {
                "title": "Open the product catalog",
                "objective": "Check that the catalog is reachable from the landing page.",
                "relevant_page_ids": [ids["root_page"], ids["catalog_page"]],
                "steps": [
                    {
                        "instruction": "Open the Catalog link.",
                        "evidence": [{"page_id": ids["root_page"], "element_ids": [ids["root_link"]]}],
                    },
                    {
                        "instruction": "Verify the Sort products button is visible.",
                        "evidence": [{"page_id": ids["catalog_page"], "element_ids": [ids["sort_button"]]}],
                    },
                ],
                "expected_outcome": "The catalog page is displayed with its product sorting control.",
            }
        ],
    )
    return draft.model_dump_json()


def _loaded_pages(session_factory, exploration_id):
    with session_factory() as session:
        return list(
            session.scalars(
                select(ExplorationPage)
                .options(selectinload(ExplorationPage.elements))
                .where(ExplorationPage.exploration_id == exploration_id)
                .order_by(ExplorationPage.depth)
            )
        )


def _run_manager(session_factory, exploration_id, provider):
    settings = Settings(_env_file=None, ai_model="test-model")
    with session_factory() as session:
        session.add(
            ExplorationAnalysis(
                exploration_id=exploration_id,
                status="queued",
                attempt_count=1,
            )
        )
        session.commit()
    manager = AnalysisManager(
        session_factory,
        settings,
        analyzer_factory=lambda: AIAnalysisService(settings, provider=provider),
    )

    async def run():
        await manager.start()
        manager.enqueue(exploration_id)
        await asyncio.wait_for(asyncio.shield(manager._tasks[exploration_id]), timeout=5)
        await manager.stop()

    asyncio.run(run())


def test_valid_ai_analysis_is_evidence_bound_and_persisted(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    provider = StubProvider(_valid_draft(ids))

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.model == "test-model"
        assert analysis.result["potential_workflows"][0]["steps"][0]["evidence"][0]["element_ids"] == [
            str(ids["root_link"])
        ]
        assert analysis.result["warnings"] == []
        assert len(analysis.result["forms_and_fields"]) == 1
        assert analysis.result["forms_and_fields"][0]["form_element_ids"] == [str(ids["form"])]
        assert analysis.result["forms_and_fields"][0]["field_element_ids"] == [str(ids["field"])]
        assert session.query(ExplorationAnalysis).filter_by(exploration_id=exploration_id).count() == 1
        cases = session.scalars(
            select(TestCase).where(
                TestCase.exploration_id == exploration_id,
                TestCase.source == "ai_generated",
                TestCase.is_current.is_(True),
            )
        ).all()
        assert len(cases) == 1
        assert cases[0].source_analysis_id == analysis.id
        assert cases[0].definition["execution_kind"] == "workflow"
    assert provider.user_payload is not None
    assert provider.user_payload["exploration"]["saved_element_count"] == 5
    assert "screenshot" not in json.dumps(provider.user_payload)
    assert provider.response_schema is not None
    evidence_schema = provider.response_schema["$defs"]["AnalysisEvidence"]
    assert set(evidence_schema["properties"]["page_id"]["enum"]) == {
        str(ids["root_page"]), str(ids["catalog_page"])
    }
    assert set(evidence_schema["properties"]["element_ids"]["items"]["enum"]) == {
        str(ids["root_link"]),
        str(ids["form"]),
        str(ids["field"]),
        str(ids["search_button"]),
        str(ids["sort_button"]),
    }
    assert evidence_schema["required"] == ["page_id", "element_ids"]
    assert evidence_schema["additionalProperties"] is False


def test_incomplete_workflow_is_discarded_and_valid_partial_analysis_is_persisted(
    analysis_database, caplog
) -> None:
    session_factory, exploration_id, ids = analysis_database
    payload = json.loads(_valid_draft(ids))
    incomplete_workflow = json.loads(json.dumps(payload["potential_workflows"][0]))
    incomplete_workflow.pop("title")
    incomplete_workflow["objective"] = "SENSITIVE_WORKFLOW_SENTINEL"
    payload["potential_workflows"].append(incomplete_workflow)
    provider = StubProvider(json.dumps(payload))

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.result is not None
        assert len(analysis.result["potential_workflows"]) == 1
        assert analysis.result["potential_workflows"][0]["title"] == "Open the product catalog"
        assert analysis.result["warnings"] == [
            {"collection": "potential_workflows", "discarded_count": 1}
        ]
        assert "SENSITIVE_WORKFLOW_SENTINEL" not in json.dumps(analysis.result)
    assert provider.complete_calls == 1
    warning_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "ai_analysis_optional_items_discarded"
    )
    formatted = json.loads(JsonFormatter().format(warning_record))
    assert formatted["discarded_collections"] == [
        {
            "collection": "potential_workflows",
            "discarded_count": 1,
            "schema_invalid_count": 1,
            "evidence_invalid_count": 0,
            "limit_discarded_count": 0,
        }
    ]
    assert "SENSITIVE_WORKFLOW_SENTINEL" not in json.dumps(formatted)


def test_optional_workflow_with_invalid_evidence_is_discarded(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    payload = json.loads(_valid_draft(ids))
    payload["potential_workflows"][0]["steps"][0]["evidence"][0]["element_ids"] = [
        str(uuid4())
    ]
    provider = StubProvider(json.dumps(payload))

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.result is not None
        assert analysis.result["potential_workflows"] == []
        assert analysis.result["warnings"] == [
            {"collection": "potential_workflows", "discarded_count": 1}
        ]
    assert provider.complete_calls == 1


def test_missing_core_field_still_fails_with_precise_validation_error(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    payload = json.loads(_valid_draft(ids))
    payload.pop("website_purpose")
    provider = StubProvider(json.dumps(payload))

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "failed"
        assert analysis.result is None
        assert "website_purpose (missing)" in analysis.error_message
    assert provider.complete_calls == 1


def test_actual_response_schema_is_strict_and_accepts_valid_draft(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    pages = _loaded_pages(session_factory, exploration_id)
    schema = _analysis_response_schema(
        pages,
        {ids["root_link"], ids["form"], ids["field"], ids["search_button"], ids["sort_button"]},
    )

    AnalysisDraft.model_validate_json(_valid_draft(ids))

    def assert_strict_objects(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                properties = value.get("properties", {})
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(properties)
            for nested in value.values():
                assert_strict_objects(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_strict_objects(nested)

    assert_strict_objects(schema)


def test_worker_persists_model_selected_by_router(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    provider = StubProvider(
        _valid_draft(ids), response_model="google/gemma-4-26b-a4b-it:free"
    )

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.model == "google/gemma-4-26b-a4b-it:free"


def test_invalid_ai_output_is_persisted_as_failed(analysis_database) -> None:
    session_factory, exploration_id, _ = analysis_database

    _run_manager(session_factory, exploration_id, StubProvider("{not json"))

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "failed"
        assert analysis.result is None
        assert analysis.error_message.startswith(
            "The AI provider returned invalid core analysis fields:"
        )


def test_provider_failure_is_persisted_with_safe_message(analysis_database) -> None:
    session_factory, exploration_id, _ = analysis_database
    provider_error = AIProviderError(
        "ai_provider_rate_limited", "The AI provider rate limited this analysis. Retry later."
    )

    _run_manager(session_factory, exploration_id, StubProvider(provider_error))

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "failed"
        assert analysis.error_message == provider_error.public_message


def test_selected_router_model_is_persisted_when_output_validation_fails(analysis_database) -> None:
    session_factory, exploration_id, _ = analysis_database
    provider_error = AIProviderError(
        "ai_analysis_invalid_evidence", "The AI provider referenced unsaved evidence."
    )
    provider = StubProvider(provider_error, response_model="recursive/flint-v1.0:free")

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "failed"
        assert analysis.model == "recursive/flint-v1.0:free"
        assert analysis.result is None


def test_invalid_core_evidence_is_rejected(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    invalid = json.loads(_valid_draft(ids))
    invalid["website_purpose"]["evidence"][0]["page_id"] = str(uuid4())
    provider = StubProvider(json.dumps(invalid))
    pages = _loaded_pages(session_factory, exploration_id)
    service = AIAnalysisService(Settings(_env_file=None), provider=provider)

    with pytest.raises(AIProviderError, match="invalid core evidence: unknown page reference"):
        asyncio.run(service.analyze("https://shop.example/", pages))


def test_invalid_optional_navigation_target_is_discarded(analysis_database) -> None:
    session_factory, exploration_id, ids = analysis_database
    payload = json.loads(_valid_draft(ids))
    payload["main_navigation"][0]["target_page_id"] = str(uuid4())
    provider = StubProvider(json.dumps(payload))

    _run_manager(session_factory, exploration_id, provider)

    with session_factory() as session:
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        assert analysis is not None
        assert analysis.status == "completed"
        assert analysis.result["main_navigation"] == []
        assert analysis.result["warnings"] == [
            {"collection": "main_navigation", "discarded_count": 1}
        ]


def test_provider_without_configuration_fails_explicitly() -> None:
    provider = OpenAICompatibleProvider(Settings(_env_file=None))

    with pytest.raises(AIProviderError, match="AI analysis is not configured") as exc_info:
        asyncio.run(provider.complete(system_prompt="prompt", user_payload={}))

    assert exc_info.value.code == "ai_provider_not_configured"


def test_provider_rate_limit_is_reported_without_exposing_provider_body(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://provider.example/v1",
        ai_model="test-model",
    )
    provider = OpenAICompatibleProvider(settings)

    def rate_limited(request, timeout):
        raise HTTPError(request.full_url, 429, "rate limited", None, None)

    monkeypatch.setattr("app.services.ai_provider.urlopen", rate_limited)

    with pytest.raises(AIProviderError, match="rate limited this analysis") as exc_info:
        asyncio.run(provider.complete(system_prompt="prompt", user_payload={}))

    assert exc_info.value.code == "ai_provider_rate_limited"


def test_provider_captures_selected_model_from_response(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://openrouter.ai/api/v1",
        ai_model="openrouter/free",
    )
    provider = OpenAICompatibleProvider(settings)
    response_body = json.dumps(
        {
            "model": "google/gemma-4-26b-a4b-it:free",
            "choices": [{"message": {"content": "{\"ok\":true}"}}],
        }
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return response_body

    captured = {}

    def successful_response(request, timeout):
        captured["request"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_provider.urlopen", successful_response)

    result = asyncio.run(
        provider.complete(
            system_prompt="Return JSON.",
            user_payload={"check": "provider"},
        )
    )

    assert result == "{\"ok\":true}"
    assert captured["request"]["model"] == "openrouter/free"
    assert captured["request"]["response_format"] == {"type": "json_object"}
    assert provider.response_model == "google/gemma-4-26b-a4b-it:free"


def test_provider_sends_strict_schema_and_requires_supported_openrouter_route(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://openrouter.ai/api/v1",
        ai_model="openrouter/free",
    )
    provider = OpenAICompatibleProvider(settings)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    captured = {}

    def successful_response(request, timeout):
        captured["request"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_provider.urlopen", successful_response)
    response_schema = {
        "type": "object",
        "properties": {"page_id": {"type": "string", "enum": ["saved-page-id"]}},
        "required": ["page_id"],
        "additionalProperties": False,
    }

    asyncio.run(
        provider.complete(
            system_prompt="Return the schema.",
            user_payload={"page_id": "saved-page-id"},
            response_schema=response_schema,
        )
    )

    request = captured["request"]
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "software_intelligence_analysis",
            "strict": True,
            "schema": response_schema,
        },
    }
    assert request["provider"] == {"require_parameters": True}


def test_gemma_free_uses_supported_json_mode_with_required_parameters(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://openrouter.ai/api/v1",
        ai_model="google/gemma-4-26b-a4b-it:free",
    )
    provider = OpenAICompatibleProvider(settings)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"model":"google/gemma-4-26b-a4b-it:free","choices":[{"finish_reason":"stop","message":{"content":"{\\"ok\\":true}"}}]}'

    captured = {}

    def successful_response(request, timeout):
        captured["request"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("app.services.ai_provider.urlopen", successful_response)
    result = asyncio.run(
        provider.complete(
            system_prompt="Return a JSON object.",
            user_payload={"check": "diagnostic"},
            response_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        )
    )

    assert result == '{"ok":true}'
    assert captured["request"]["response_format"] == {"type": "json_object"}
    assert captured["request"]["provider"] == {"require_parameters": True}


def test_provider_extracts_content_and_records_sanitized_response_metadata(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://openrouter.ai/api/v1",
        ai_model="openrouter/free",
    )
    provider = OpenAICompatibleProvider(settings)
    response_body = json.dumps(
        {
            "model": "provider/model:free",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "```json\n{\"ok\":true}\n```",
                        "reasoning_details": [{"type": "omitted"}],
                    },
                }
            ],
        }
    ).encode()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return response_body

    monkeypatch.setattr("app.services.ai_provider.urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = asyncio.run(provider.complete(system_prompt="Return JSON.", user_payload={}))

    assert result == '{"ok":true}'
    assert provider.response_diagnostics == {
        "response_type": "str",
        "finish_reason": "stop",
        "model": "provider/model:free",
        "requested_model": "openrouter/free",
        "response_length": len('{"ok":true}'),
        "has_reasoning": True,
        "has_refusal": False,
        "has_parsed_field": False,
        "validation_error": None,
    }


@pytest.mark.parametrize(
    ("choice", "expected_code", "response_type", "validation_error"),
    [
        ({"finish_reason": "length", "message": {"content": "{}"}}, "ai_provider_truncated_response", "str", "truncated"),
        ({"finish_reason": "stop", "message": {"content": None, "parsed": {"ok": True}}}, "ai_provider_invalid_response", "NoneType", "empty_content"),
        ({"finish_reason": "stop", "message": {"content": "{}", "refusal": "refused"}}, "ai_provider_refused", "str", "refusal"),
    ],
)
def test_provider_rejects_truncated_empty_or_refusal_response(
    monkeypatch, choice, expected_code, response_type, validation_error
) -> None:
    settings = Settings(
        _env_file=None,
        ai_api_base_url="https://openrouter.ai/api/v1",
        ai_model="openrouter/free",
    )
    provider = OpenAICompatibleProvider(settings)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"choices": [choice]}).encode()

    monkeypatch.setattr("app.services.ai_provider.urlopen", lambda *_args, **_kwargs: FakeResponse())

    with pytest.raises(AIProviderError) as exc_info:
        asyncio.run(provider.complete(system_prompt="Return JSON.", user_payload={}))

    assert exc_info.value.code == expected_code
    assert provider.response_diagnostics["response_type"] == response_type
    assert provider.response_diagnostics["validation_error"] == validation_error
    if choice["message"].get("parsed"):
        assert provider.response_diagnostics["has_parsed_field"] is True


def test_analysis_validation_logs_field_errors_without_response_content(analysis_database, caplog) -> None:
    session_factory, exploration_id, _ = analysis_database
    marker = "SENSITIVE_MODEL_OUTPUT_SENTINEL"
    provider = StubProvider(
        json.dumps({"unexpected": marker}),
        response_diagnostics={
            "model": "router/model:free",
            "response_type": "str",
            "finish_reason": "stop",
            "response_length": 45,
        },
    )
    pages = _loaded_pages(session_factory, exploration_id)
    service = AIAnalysisService(Settings(_env_file=None), provider=provider)

    with pytest.raises(AIProviderError):
        asyncio.run(service.analyze("https://shop.example/", pages))

    record = next(
        item for item in caplog.records if item.getMessage() == "ai_analysis_output_validation_failed"
    )
    formatted = json.loads(JsonFormatter().format(record))
    assert formatted["validation_errors"]
    assert formatted["model"] == "router/model:free"
    assert formatted["response_type"] == "str"
    assert formatted["finish_reason"] == "stop"
    assert formatted["response_length"] == 45
    assert marker not in json.dumps(formatted)
