from typing import Any

from fastapi.encoders import jsonable_encoder
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from .db import Database

_INSERT = """
INSERT INTO refusal_audit_logs (
    attempted_by, actor_role, action, reason_code, http_status, request_path,
    idempotency_key, source_account_id, destination_account_id, requested_amount,
    account_balance_snapshot, details
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _params(
    *,
    user_id: Any,
    role: str | None,
    action: str,
    reason_code: str,
    http_status: int,
    request_path: str | None,
    idempotency_key: str | None = None,
    source_account_id: Any = None,
    destination_account_id: Any = None,
    requested_amount: int | None = None,
    balance_snapshot: int | None = None,
    details: dict | None = None,
) -> list:
    # Clamp amounts that don't fit BIGINT so a hostile payload can't break auditing.
    if requested_amount is not None and not (-(2**63) <= requested_amount < 2**63):
        details = {**(details or {}), "requested_amount_out_of_range": str(requested_amount)}
        requested_amount = None
    return [
        user_id,
        role,
        action,
        reason_code,
        http_status,
        request_path,
        idempotency_key,
        source_account_id,
        destination_account_id,
        requested_amount,
        balance_snapshot,
        Jsonb(jsonable_encoder(details or {})),
    ]


async def log_refusal(conn: AsyncConnection, **kwargs) -> None:
    """Log inside the caller's transaction (commits with it)."""
    await conn.execute(_INSERT, _params(**kwargs))


async def log_refusal_standalone(db: Database, **kwargs) -> None:
    """Log in a separate, short transaction. Never let auditing mask the real error."""
    try:
        async with db.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(_INSERT, _params(**kwargs))
    except Exception:  # pragma: no cover - audit logging is best effort here
        import logging

        logging.getLogger("ledgerline").exception("failed to write refusal audit log")
