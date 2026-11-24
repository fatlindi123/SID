from pathlib import Path
import sys
from typing import Annotated
from importlib.util import find_spec

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.core.config import BACKEND_ROOT, Settings
from app.schemas.system import SystemStatusResponse
from app.services.health import check_database

router = APIRouter(prefix="/system", tags=["system"])


def browser_automation_available(settings: Settings) -> bool:
    if find_spec("playwright") is None:
        return False
    browser_root = settings.playwright_browsers_path
    if not browser_root.is_absolute():
        browser_root = BACKEND_ROOT / browser_root
    if sys.platform == "win32":
        candidates = browser_root.glob("chromium-*/chrome-win*/chrome.exe")
    elif sys.platform == "darwin":
        candidates = browser_root.glob("chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium")
    else:
        candidates = browser_root.glob("chromium-*/chrome-linux*/chrome")
    try:
        return any(candidate.is_file() for candidate in candidates)
    except Exception:
        return False


@router.get("/status", response_model=SystemStatusResponse, summary="Read safe application and dependency status")
def get_system_status(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> SystemStatusResponse:
    settings: Settings = request.app.state.settings
    try:
        check_database(session)
        database_status = "healthy"
    except SQLAlchemyError:
        database_status = "unhealthy"

    ai_configured = bool(settings.ai_api_base_url and settings.ai_model)
    return SystemStatusResponse(
        version=request.app.version,
        environment=settings.app_environment,
        database=database_status,
        ai_provider="configured" if ai_configured else "not_configured",
        browser_automation="available" if browser_automation_available(settings) else "unavailable",
    )
