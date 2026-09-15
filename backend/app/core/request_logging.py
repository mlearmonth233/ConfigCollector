"""Per-request logging and error capture for the API process.

Every request gets one line: method, path, status, duration and, when the
caller sent a valid token, the user id it belongs to. Successful GETs are
DEBUG (the frontend polls job pages every few seconds; at INFO that would
drown everything else) - everything that changes something, and every
failure, is INFO or above.

An unhandled exception is logged with its full traceback under a short
reference id, and the client gets a 500 carrying that same id, so an
"Internal server error (ref 3f9c2a1b)" on screen can be matched to the
exact traceback in packrat-api.log.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.security import decode_access_token

log = logging.getLogger("app.request")


def _caller(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        payload = decode_access_token(auth[7:])
        if payload and payload.get("sub"):
            return f"user={payload['sub']}"
    return "anonymous"


def _client(request: Request) -> str:
    return request.client.host if request.client else "-"


def install_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            ref = uuid.uuid4().hex[:8]
            elapsed_ms = (time.perf_counter() - started) * 1000
            log.exception(
                "%s %s -> 500 in %.0fms (%s from %s) ref=%s",
                request.method,
                request.url.path,
                elapsed_ms,
                _caller(request),
                _client(request),
                ref,
            )
            return JSONResponse(status_code=500, content={"detail": f"Internal server error (ref {ref})", "ref": ref})
        elapsed_ms = (time.perf_counter() - started) * 1000
        status = response.status_code
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        elif request.method == "GET" or request.method in ("HEAD", "OPTIONS"):
            level = logging.DEBUG
        else:
            level = logging.INFO
        if log.isEnabledFor(level):
            query = f"?{request.url.query}" if request.url.query else ""
            log.log(level, "%s %s%s -> %s in %.0fms (%s from %s)", request.method, request.url.path, query, status, elapsed_ms, _caller(request), _client(request))
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _log_http_exception(request: Request, exc: StarletteHTTPException):
        # 401s from expired tokens are routine (INFO); anything else the API
        # refused is worth a WARNING with the reason the caller was given.
        level = logging.INFO if exc.status_code == 401 else logging.WARNING
        log.log(level, "%s %s refused with %s: %s (%s)", request.method, request.url.path, exc.status_code, exc.detail, _caller(request))
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _log_validation_error(request: Request, exc: RequestValidationError):
        problems = "; ".join(f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}" for e in exc.errors())
        log.warning("%s %s rejected as invalid (422): %s (%s)", request.method, request.url.path, problems, _caller(request))
        return await request_validation_exception_handler(request, exc)
