"""Idempotency engine.

One request = one database transaction:

    BEGIN
      pg_try_advisory_xact_lock(user, key)   -- someone else processing this key? -> 409
      SELECT idempotency_keys row
        COMPLETED + same fingerprint  -> return cached response (no re-execution)
        COMPLETED + other fingerprint -> 422 IDEMPOTENCY_KEY_REUSED
        IN_FLIGHT                     -> 409 CONCURRENT_CONFLICT
      INSERT key (IN_FLIGHT)
      run handler: lock accounts, post ledger rows, update balances (or refuse)
      UPDATE key -> COMPLETED with response_code / response_body
    COMMIT

Because the key row, the ledger rows and the cached response commit together,
there is no window where money moved but the response was not cached, and no
stuck IN_FLIGHT key after a crash (the whole transaction rolls back).
"""

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from psycopg import AsyncConnection, errors as pg_errors
from psycopg.types.json import Jsonb

from . import errors
from .db import Database, fetchone
from .errors import ApiError, Refused, error_body
from .hashing import request_fingerprint
from .refusals import log_refusal, log_refusal_standalone
from .security import Principal

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-:.]{1,255}$")
_MAX_ATTEMPTS = 4


@dataclass
class Outcome:
    status_code: int
    body: dict
    transaction_id: UUID | None = None


@dataclass
class IdempotentResult:
    status_code: int
    body: dict
    replayed: bool


Handler = Callable[[AsyncConnection], Awaitable[Outcome]]


async def validate_key(db: Database, principal: Principal, key: str | None, action: str, path: str) -> str:
    if key is None or key == "":
        await log_refusal_standalone(
            db, user_id=principal.id, role=principal.role, action=action,
            reason_code=errors.MISSING_IDEMPOTENCY_KEY, http_status=400, request_path=path,
        )
        raise ApiError(400, errors.MISSING_IDEMPOTENCY_KEY, "The Idempotency-Key header is required.")
    if not _KEY_PATTERN.match(key):
        await log_refusal_standalone(
            db, user_id=principal.id, role=principal.role, action=action,
            reason_code=errors.INVALID_IDEMPOTENCY_KEY, http_status=400, request_path=path,
            details={"key_length": len(key)},
        )
        raise ApiError(
            400, errors.INVALID_IDEMPOTENCY_KEY,
            "Idempotency-Key must be 1-255 characters of [A-Za-z0-9_-:.].",
        )
    return key


async def execute_idempotent(
    db: Database,
    *,
    principal: Principal,
    key: str,
    method: str,
    path: str,
    payload: dict[str, Any],
    action: str,
    handler: Handler,
) -> IdempotentResult:
    fingerprint = request_fingerprint(method, path, payload)
    audit = dict(user_id=principal.id, role=principal.role, action=action, request_path=path, idempotency_key=key)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with db.pool.connection() as conn:
                async with conn.transaction():
                    return await _run_once(conn, principal, key, method, path, fingerprint, audit, handler)
        except (pg_errors.DeadlockDetected, pg_errors.SerializationFailure):
            # Locks are taken in a fixed order so this should not happen; retry defensively.
            if attempt == _MAX_ATTEMPTS:
                raise
            await asyncio.sleep(random.uniform(0.005, 0.05) * attempt)
        except pg_errors.LockNotAvailable:
            await log_refusal_standalone(
                db, **audit, reason_code=errors.CONCURRENT_CONFLICT, http_status=503,
                details={"reason": "lock_timeout"},
            )
            raise ApiError(
                503, errors.CONCURRENT_CONFLICT,
                "The account is busy. Retry with the same Idempotency-Key.",
            )
    raise AssertionError("unreachable")


async def _run_once(
    conn: AsyncConnection,
    principal: Principal,
    key: str,
    method: str,
    path: str,
    fingerprint: str,
    audit: dict,
    handler: Handler,
) -> IdempotentResult:
    got_lock = await fetchone(
        conn,
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 7)) AS got",
        [f"idem:{principal.id}:{key}"],
    )
    if not got_lock["got"]:
        # Another request with this key is executing right now.
        await log_refusal(conn, **audit, reason_code=errors.CONCURRENT_CONFLICT, http_status=409,
                          details={"reason": "idempotency_key_in_flight"})
        return IdempotentResult(
            409,
            error_body(errors.CONCURRENT_CONFLICT,
                       "A request with this Idempotency-Key is already in flight. Retry shortly."),
            False,
        )

    existing = await fetchone(
        conn,
        """SELECT request_method, request_path, request_hash, status, response_code, response_body
             FROM idempotency_keys WHERE user_id = %s AND idempotency_key = %s""",
        [principal.id, key],
    )
    if existing is not None:
        if (existing["request_hash"] != fingerprint or existing["request_path"] != path
                or existing["request_method"] != method):
            await log_refusal(conn, **audit, reason_code=errors.IDEMPOTENCY_KEY_REUSED, http_status=422,
                              details={"original_path": existing["request_path"]})
            return IdempotentResult(
                422,
                error_body(errors.IDEMPOTENCY_KEY_REUSED,
                           "This Idempotency-Key was already used with a different request."),
                False,
            )
        if existing["status"] == "COMPLETED":
            return IdempotentResult(existing["response_code"], existing["response_body"], True)
        await log_refusal(conn, **audit, reason_code=errors.CONCURRENT_CONFLICT, http_status=409,
                          details={"reason": "idempotency_key_in_flight"})
        return IdempotentResult(
            409, error_body(errors.CONCURRENT_CONFLICT, "Request is currently processing."), False
        )

    await conn.execute(
        """INSERT INTO idempotency_keys
               (user_id, idempotency_key, request_method, request_path, request_hash, status)
           VALUES (%s, %s, %s, %s, %s, 'IN_FLIGHT')""",
        [principal.id, key, method, path, fingerprint],
    )

    try:
        outcome = await handler(conn)
    except Refused as refusal:
        await log_refusal(
            conn, **audit,
            reason_code=refusal.code,
            http_status=refusal.status_code,
            source_account_id=refusal.source_account_id,
            destination_account_id=refusal.destination_account_id,
            requested_amount=refusal.requested_amount,
            balance_snapshot=refusal.balance_snapshot,
            details=refusal.details,
        )
        outcome = Outcome(refusal.status_code, error_body(refusal.code, refusal.message, refusal.details))

    body = jsonable_encoder(outcome.body)
    await conn.execute(
        """UPDATE idempotency_keys
              SET status = 'COMPLETED', response_code = %s, response_body = %s,
                  transaction_id = %s, completed_at = now()
            WHERE user_id = %s AND idempotency_key = %s""",
        [outcome.status_code, Jsonb(body), outcome.transaction_id, principal.id, key],
    )
    return IdempotentResult(outcome.status_code, body, False)
