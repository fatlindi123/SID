from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import install_exception_handlers
from app.core.logging import configure_logging, request_context_middleware
from app.db.session import SessionLocal
from app.services.analysis_worker import AnalysisManager
from app.services.exploration_worker import ExplorationManager
from app.services.test_execution_worker import TestExecutionManager


def create_app(
    manager_factory: Callable[[], Any] | None = None,
    analysis_manager_factory: Callable[[], Any] | None = None,
    test_execution_manager_factory: Callable[[], Any] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        manager = (
            manager_factory()
            if manager_factory is not None
            else ExplorationManager(SessionLocal, settings)
        )
        analysis_manager = (
            analysis_manager_factory()
            if analysis_manager_factory is not None
            else AnalysisManager(SessionLocal, settings)
            if manager_factory is None
            else None
        )
        test_execution_manager = (
            test_execution_manager_factory()
            if test_execution_manager_factory is not None
            else TestExecutionManager(SessionLocal, settings)
            if manager_factory is None
            else None
        )
        application.state.exploration_manager = manager
        application.state.analysis_manager = analysis_manager
        application.state.test_execution_manager = test_execution_manager
        await manager.start()
        if analysis_manager is not None:
            await analysis_manager.start()
        if test_execution_manager is not None:
            await test_execution_manager.start()
        try:
            yield
        finally:
            if test_execution_manager is not None:
                await test_execution_manager.stop()
            if analysis_manager is not None:
                await analysis_manager.stop()
            await manager.stop()

    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Backend API for SID.",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.middleware("http")(request_context_middleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
    )
    install_exception_handlers(application)
    application.include_router(api_router, prefix=settings.api_v1_prefix)
    return application


app = create_app()
