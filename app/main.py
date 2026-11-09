import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from uuid import UUID

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import jwt
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors

from . import errors
from .config import Settings, get_settings
from .db import Database
from .errors import ApiError, error_body
from .refusals import log_refusal_standalone
from .routers import accounts, audit, auth, ledger, money, users
from .security import bearer_token, decode_token

log = logging.getLogger("ledgerline")

_MONEY_ACTIONS = {"/v1/deposits": "DEPOSIT", "/v1/withdrawals": "WITHDRAWAL", "/v1/transfers": "TRANSFER"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await app.state.db.open()
        try:
            yield
        finally:
            await app.state.db.close()

    app = FastAPI(
        title="LedgerLine",
        version="1.0.0",
        description="Double-entry ledger and money-movement engine with provable invariants.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = Database(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["Idempotent-Replayed"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        problems = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        action = _MONEY_ACTIONS.get(request.url.path)
        if action is not None:
            # Malformed money requests (negative, fractional, string amounts...) are adversarial: audit them.
            await _audit_bad_money_request(request, action, exc.body, problems)
        return JSONResponse(
            status_code=422,
            content=error_body(errors.VALIDATION_ERROR, "Request validation failed.", {"errors": problems}),
        )

    @app.exception_handler(pg_errors.RaiseException)
    async def invariant_guard_handler(request: Request, exc: pg_errors.RaiseException):
        # A database invariant trigger fired. The whole transaction was rolled back.
        log.error("database invariant guard tripped on %s: %s", request.url.path, exc)
        await log_refusal_standalone(
            request.app.state.db, user_id=None, role=None, action=f"{request.method} {request.url.path}",
            reason_code=errors.INVARIANT_GUARD_TRIPPED, http_status=500, request_path=request.url.path,
            details={"error": str(exc).splitlines()[0]},
        )
        return JSONResponse(
            status_code=500,
            content=error_body(errors.INVARIANT_GUARD_TRIPPED, "A ledger invariant check rejected this operation."),
        )

    for router in (auth.router, users.router, accounts.router, money.router, ledger.router, audit.router):
        app.include_router(router, prefix="/v1")

    @app.get("/health", tags=["meta"])
    async def health():
        async with app.state.db.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ok"}

    return app


async def _audit_bad_money_request(request: Request, action: str, body, problems: list) -> None:
    user_id = role = None
    token = bearer_token(request)
    if token:
        try:
            claims = decode_token(token, request.app.state.settings.jwt_secret)
            user_id, role = UUID(claims["sub"]), claims.get("role")
        except (jwt.PyJWTError, ValueError, KeyError):
            pass
    amount = body.get("amount") if isinstance(body, dict) else None
    await log_refusal_standalone(
        request.app.state.db,
        user_id=user_id,
        role=role,
        action=action,
        reason_code=errors.VALIDATION_ERROR,
        http_status=422,
        request_path=request.url.path,
        idempotency_key=request.headers.get("idempotency-key"),
        requested_amount=amount if type(amount) is int else None,
        details={"errors": problems, "raw_amount": repr(amount)[:64] if amount is not None else None},
    )


app = create_app()
