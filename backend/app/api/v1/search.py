import re
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration, ExplorationPage
from app.models.project import Project, ProjectMetadata
from app.models.report import GeneratedReport
from app.models.test_case import TestCase
from app.models.workflow import Workflow
from app.schemas.search import SearchDestination, SearchResponse, SearchResult

router = APIRouter(prefix="/search", tags=["search"])
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|cookie|session|credential)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_LONG_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{48,}$")
_SENSITIVE_PATH_SEGMENT = re.compile(r"(?i)^(token|session|secret|auth|password|reset|api[-_]?key)$")
_CATEGORY_LIMIT = 4
_TOTAL_LIMIT = 20


def _safe_text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("\x00", " ").strip()
    value = _SECRET_ASSIGNMENT.sub(r"\1=[redacted]", value)
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _JWT.sub("[redacted token]", value)
    value = _EMAIL.sub("[redacted email]", value)
    return value[:limit] or None


def _safe_url(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname.encode("idna").decode("ascii")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + (f":{parsed.port}" if parsed.port is not None else "")
        path = "/".join(
            "[redacted]" if _LONG_PATH_SEGMENT.fullmatch(segment) or _SENSITIVE_PATH_SEGMENT.fullmatch(segment) else segment
            for segment in parsed.path.split("/")
        )
        return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))
    except (UnicodeError, ValueError):
        return None


def _contains(value: Any, term: str) -> bool:
    return isinstance(value, str) and term.casefold() in value.casefold()


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _rank(result: SearchResult, term: str) -> tuple[int, str, str]:
    title = result.title.casefold()
    query = term.casefold()
    score = 0 if title == query else 1 if title.startswith(query) else 2 if query in title else 3
    return score, result.title.casefold(), result.entity_type


@router.get("", response_model=SearchResponse)
def global_search(
    session: Annotated[Session, Depends(get_db_session)],
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> dict[str, Any]:
    term = q.strip()
    if len(term) < 2:
        raise HTTPException(status_code=422, detail="Search query must contain at least 2 non-space characters.")
    pattern = _like_pattern(term)
    results: list[SearchResult] = []

    project_rows = session.execute(
        select(Project, ProjectMetadata.environment)
        .outerjoin(ProjectMetadata, ProjectMetadata.project_id == Project.id)
        .where(
            or_(
                Project.name.ilike(pattern, escape="\\"),
                ProjectMetadata.environment.ilike(pattern, escape="\\"),
                Project.target_url.ilike(pattern, escape="\\") & ~Project.target_url.contains("?"),
            )
        )
        .order_by(Project.updated_at.desc(), Project.id)
        .limit(_CATEGORY_LIMIT)
    ).all()
    for project, environment in project_rows:
        title = _safe_text(project.name, 300)
        safe_url = _safe_url(project.target_url)
        safe_environment = _safe_text(environment, 100)
        if title and (
            _contains(title, term)
            or _contains(safe_url, term)
            or _contains(safe_environment, term)
        ):
            results.append(SearchResult(
                entity_type="project", id=project.id, title=title,
                context=safe_url or "Project",
                destination=SearchDestination(workspace="Projects", project_id=project.id),
            ))

    exploration_rows = session.execute(
        select(Exploration)
        .where(Exploration.start_url.ilike(pattern, escape="\\"), ~Exploration.start_url.contains("?"))
        .order_by(Exploration.created_at.desc(), Exploration.id)
        .limit(_CATEGORY_LIMIT)
    ).scalars().all()
    for exploration in exploration_rows:
        safe_url = _safe_url(exploration.start_url)
        if safe_url and _contains(safe_url, term):
            results.append(SearchResult(
                entity_type="exploration", id=exploration.id, title=safe_url,
                context=f"Exploration · {exploration.status}",
                destination=SearchDestination(workspace="Exploration", exploration_id=exploration.id),
            ))

    page_rows = session.execute(
        select(ExplorationPage, Exploration.start_url)
        .join(Exploration, Exploration.id == ExplorationPage.exploration_id)
        .where(
            or_(
                ExplorationPage.title.ilike(pattern, escape="\\"),
                ExplorationPage.url.ilike(pattern, escape="\\") & ~ExplorationPage.url.contains("?"),
            )
        )
        .order_by(ExplorationPage.discovered_at.desc(), ExplorationPage.id)
        .limit(_CATEGORY_LIMIT)
    ).all()
    for page, start_url in page_rows:
        safe_title = _safe_text(page.title, 300)
        safe_url = _safe_url(page.url)
        if not (safe_title and _contains(safe_title, term)) and not (safe_url and _contains(safe_url, term)):
            continue
        title = safe_title or safe_url or "Saved page"
        context = _safe_url(start_url) or "Saved exploration page"
        results.append(SearchResult(
            entity_type="page", id=page.id, title=title, context=context,
            destination=SearchDestination(
                workspace="Exploration", exploration_id=page.exploration_id, page_id=page.id
            ),
        ))

    case_rows = session.execute(
        select(TestCase, Exploration.start_url)
        .join(Exploration, Exploration.id == TestCase.exploration_id)
        .where(
            TestCase.name.ilike(pattern, escape="\\"),
            TestCase.is_current.is_(True),
            TestCase.deleted_at.is_(None),
        )
        .order_by(TestCase.updated_at.desc(), TestCase.id)
        .limit(_CATEGORY_LIMIT)
    ).all()
    for test_case, start_url in case_rows:
        title = _safe_text(test_case.name, 300)
        if title and _contains(title, term):
            results.append(SearchResult(
                entity_type="test_case", id=test_case.id, title=title,
                context=_safe_url(start_url) or "Saved test case",
                destination=SearchDestination(workspace="Test Cases", exploration_id=test_case.exploration_id),
            ))

    workflow_rows = session.execute(
        select(Workflow, Exploration.start_url)
        .join(Exploration, Exploration.id == Workflow.exploration_id)
        .where(Workflow.name.ilike(pattern, escape="\\"))
        .order_by(Workflow.updated_at.desc(), Workflow.id)
        .limit(_CATEGORY_LIMIT)
    ).all()
    for workflow, start_url in workflow_rows:
        title = _safe_text(workflow.name, 300)
        if title and _contains(title, term):
            results.append(SearchResult(
                entity_type="workflow", id=workflow.id, title=title,
                context=_safe_url(start_url) or "Saved workflow",
                destination=SearchDestination(workspace="Workflow Intelligence", exploration_id=workflow.exploration_id),
            ))

    report_rows = session.scalars(
        select(GeneratedReport)
        .where(GeneratedReport.scope_label.ilike(pattern, escape="\\"))
        .order_by(GeneratedReport.generated_at.desc(), GeneratedReport.id)
        .limit(_CATEGORY_LIMIT)
    ).all()
    for report in report_rows:
        title = _safe_text(report.scope_label, 300)
        if title and _contains(title, term):
            destination = SearchDestination(
                workspace="Reports", report_id=report.id,
                project_id=report.project_id, exploration_id=report.exploration_id,
            )
            results.append(SearchResult(
                entity_type="report", id=report.id, title=title,
                context=f"{report.report_type.title()} report · {report.status}",
                destination=destination,
            ))

    results.sort(key=lambda item: _rank(item, term))
    return {"results": results[:_TOTAL_LIMIT]}
