"""Every error body is an `Error` (openapi.yaml → Errors), including FastAPI's validation errors."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import Forbidden, Unauthorized
from app.schemas import Error, ErrorDetail
from app.services.errors import (
    InvalidCredentials,
    InvalidModel,
    InvalidTransition,
    MissingReason,
    NotFound,
    TooManyLoginAttempts,
)

# Response declarations for routers: {status: Error schema}.
ERROR_DESCRIPTIONS = {
    400: "Malformed request (bad JSON, wrong content type).",
    401: "No valid session, token, or signature.",
    403: "Logged in, but the action requires the admin role.",
    404: "No such change or incident.",
    409: "Not allowed in the subject's current state.",
    422: "The request failed validation; `details` lists each problem.",
    429: "Too many failed logins for this username or address; see `Retry-After`.",
    501: "The operation is part of a later phase.",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": Error, "description": ERROR_DESCRIPTIONS[code]} for code in codes}


class NotImplementedYet(Exception):
    """A contract operation whose implementation ships with a later issue (HTTP 501)."""

    def __init__(self, operation: str, where: str) -> None:
        super().__init__(f"{operation} is not implemented yet; it ships with {where}")


def error_response(
    status: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = Error(code=code, message=message, details=details)
    return JSONResponse(
        body.model_dump(mode="json", exclude_none=True), status_code=status, headers=headers
    )


def _http_code(status: int) -> str:
    try:
        return HTTPStatus(status).phrase.lower().replace(" ", "_").replace("-", "_")
    except ValueError:
        return "error"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        # A body that isn't JSON at all is a malformed request (400), not a validation error.
        if any(error.get("type") == "json_invalid" for error in errors):
            return error_response(400, "bad_request", "request body is not valid JSON")
        details = [
            ErrorDetail(
                loc=[part for part in error.get("loc", ()) if isinstance(part, str | int)],
                msg=str(error.get("msg", "")),
                type=str(error.get("type", "")),
            )
            for error in errors
        ]
        return error_response(422, "validation_error", "the request failed validation", details)

    @app.exception_handler(StarletteHTTPException)
    async def http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, _http_code(exc.status_code), str(exc.detail))

    @app.exception_handler(Unauthorized)
    async def unauthorized(_: Request, exc: Unauthorized) -> JSONResponse:
        return error_response(401, "unauthorized", str(exc))

    @app.exception_handler(InvalidCredentials)
    async def invalid_credentials(_: Request, exc: InvalidCredentials) -> JSONResponse:
        return error_response(401, "unauthorized", str(exc))

    @app.exception_handler(TooManyLoginAttempts)
    async def too_many_logins(_: Request, exc: TooManyLoginAttempts) -> JSONResponse:
        return error_response(
            429, "too_many_requests", str(exc), headers={"Retry-After": str(exc.retry_after)}
        )

    @app.exception_handler(Forbidden)
    async def forbidden(_: Request, exc: Forbidden) -> JSONResponse:
        return error_response(403, "forbidden", str(exc))

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(InvalidTransition)
    async def conflict(_: Request, exc: InvalidTransition) -> JSONResponse:
        return error_response(409, "invalid_state_transition", str(exc))

    @app.exception_handler(MissingReason)
    async def missing_reason(_: Request, exc: MissingReason) -> JSONResponse:
        return error_response(422, "validation_error", str(exc))

    @app.exception_handler(InvalidModel)
    async def invalid_model(_: Request, exc: InvalidModel) -> JSONResponse:
        return error_response(422, "validation_error", str(exc))

    @app.exception_handler(NotImplementedYet)
    async def not_implemented(_: Request, exc: NotImplementedYet) -> JSONResponse:
        return error_response(501, "not_implemented", str(exc))
