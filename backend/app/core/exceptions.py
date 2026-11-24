import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_context

logger = logging.getLogger("software_intelligence.errors")


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = jsonable_encoder(details)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "request_id": request_id_context.get(),
        },
    )


def install_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        issues = [
            {
                "location": issue.get("loc", ()),
                "message": issue.get("msg", "Invalid value"),
                "code": issue.get("type", "value_error"),
            }
            for issue in exc.errors()
        ]
        return _error_response(
            422,
            "validation_error",
            "The request did not match the expected format.",
            issues,
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code", "http_error"))
            message = str(detail.get("message", "The request could not be completed."))
            details = detail.get("details")
        else:
            code = "http_error"
            message = str(detail)
            details = None
        return _error_response(exc.status_code, code, message, details)

    @application.exception_handler(SQLAlchemyError)
    async def database_exception_handler(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        logger.exception(
            "database_operation_failed",
            extra={"error_code": "database_unavailable"},
        )
        return _error_response(
            503,
            "database_unavailable",
            "The database is currently unavailable.",
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={"error_code": "internal_server_error"},
        )
        return _error_response(
            500,
            "internal_server_error",
            "An unexpected error occurred.",
        )
