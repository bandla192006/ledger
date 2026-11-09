from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .. import errors
from ..db import Database, fetchall, fetchone
from ..errors import ApiError
from ..refusals import log_refusal_standalone
from ..security import Principal, get_current_user, get_db, require_roles

router = APIRouter(tags=["ledger"])

_TX_COLUMNS = """t.id, t.type, t.amount, t.currency, t.initiated_by, u.full_name AS initiated_by_name,
                 t.idempotency_key, t.description, t.external_ref, t.created_at"""


@router.get("/transactions")
async def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    type: str | None = Query(None, pattern="^(DEPOSIT|WITHDRAWAL|TRANSFER)$"),
    principal: Principal = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Admins/auditors see the global list; holders see transactions touching their accounts."""
    scope_sql, params = "", []
    if principal.role == "ACCOUNT_HOLDER":
        scope_sql = """AND EXISTS (SELECT 1 FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
                                    WHERE e.transaction_id = t.id AND a.owner_id = %s)"""
        params.append(principal.id)
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            f"""SELECT {_TX_COLUMNS} FROM transactions t JOIN users u ON u.id = t.initiated_by
                 WHERE (%s::text IS NULL OR t.type = %s::text) {scope_sql}
                 ORDER BY t.created_at DESC, t.id
                 LIMIT %s OFFSET %s""",
            [type, type, *params, limit, offset],
        )
    return {"transactions": rows, "limit": limit, "offset": offset}


@router.get("/transactions/{transaction_id}")
async def get_transaction(
    transaction_id: UUID,
    request: Request,
    principal: Principal = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        tx = await fetchone(
            conn,
            f"SELECT {_TX_COLUMNS} FROM transactions t JOIN users u ON u.id = t.initiated_by WHERE t.id = %s",
            [transaction_id],
        )
        entries = await fetchall(
            conn,
            """SELECT e.id, e.account_id, a.name AS account_name, a.owner_id, e.account_seq, e.entry_type,
                      e.amount, e.balance_after, e.currency, e.prev_hash, e.entry_hash, e.created_at
                 FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
                WHERE e.transaction_id = %s ORDER BY e.entry_type DESC""",
            [transaction_id],
        )
    if principal.role == "ACCOUNT_HOLDER":
        entries = [e for e in entries if e["owner_id"] == principal.id]
        if tx is None or not entries:
            await log_refusal_standalone(
                db, user_id=principal.id, role=principal.role, action=f"GET {request.url.path}",
                reason_code=errors.UNAUTHORIZED, http_status=403, request_path=request.url.path,
            )
            raise ApiError(403, errors.UNAUTHORIZED, "You can only view your own transactions.")
    if tx is None:
        raise ApiError(404, "TRANSACTION_NOT_FOUND", "Transaction not found.")
    return {"transaction": {**tx, "entries": entries}}


@router.get("/ledger/entries")
async def global_ledger(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    account_id: UUID | None = None,
    principal: Principal = Depends(require_roles("ADMIN", "AUDITOR")),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT e.id, e.transaction_id, t.type AS transaction_type, e.account_id, a.name AS account_name,
                      u.full_name AS owner_name, e.account_seq, e.entry_type, e.amount, e.balance_after,
                      e.currency, e.prev_hash, e.entry_hash, e.created_at
                 FROM ledger_entries e
                 JOIN transactions t ON t.id = e.transaction_id
                 JOIN accounts a ON a.id = e.account_id
                 LEFT JOIN users u ON u.id = a.owner_id
                WHERE (%s::uuid IS NULL OR e.account_id = %s::uuid)
                ORDER BY e.created_at DESC, e.transaction_id, e.entry_type DESC
                LIMIT %s OFFSET %s""",
            [account_id, account_id, limit, offset],
        )
    return {"entries": rows, "limit": limit, "offset": offset}
