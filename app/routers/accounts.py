from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from psycopg import errors as pg_errors

from .. import errors
from ..db import Database, fetchall, fetchone
from ..errors import ApiError
from ..refusals import log_refusal_standalone
from ..schemas import AccountStatusRequest, CreateAccountRequest
from ..security import Principal, get_current_user, get_db, require_roles

router = APIRouter(prefix="/accounts", tags=["accounts"])

_ACCOUNT_COLUMNS = """a.id, a.owner_id, u.full_name AS owner_name, u.email AS owner_email, a.name,
                      a.account_type, a.system_code, a.currency, a.balance, a.status, a.created_at"""


async def load_visible_account(db: Database, principal: Principal, account_id: UUID, request: Request) -> dict:
    """Holders may only see their own accounts; admins and auditors see all."""
    async with db.pool.connection() as conn:
        acct = await fetchone(
            conn,
            f"SELECT {_ACCOUNT_COLUMNS} FROM accounts a LEFT JOIN users u ON u.id = a.owner_id WHERE a.id = %s",
            [account_id],
        )
    if principal.role == "ACCOUNT_HOLDER" and (acct is None or str(acct["owner_id"]) != str(principal.id)):
        await log_refusal_standalone(
            db, user_id=principal.id, role=principal.role, action=f"{request.method} {request.url.path}",
            reason_code=errors.UNAUTHORIZED, http_status=403, request_path=request.url.path,
            source_account_id=account_id,
        )
        raise ApiError(403, errors.UNAUTHORIZED, "You can only view your own accounts.")
    if acct is None:
        raise ApiError(404, errors.ACCOUNT_NOT_FOUND, "Account not found.")
    return acct


@router.get("")
async def list_accounts(principal: Principal = Depends(get_current_user), db: Database = Depends(get_db)):
    async with db.pool.connection() as conn:
        if principal.role == "ACCOUNT_HOLDER":
            rows = await fetchall(
                conn,
                f"""SELECT {_ACCOUNT_COLUMNS} FROM accounts a LEFT JOIN users u ON u.id = a.owner_id
                     WHERE a.owner_id = %s ORDER BY a.created_at, a.name""",
                [principal.id],
            )
        else:
            rows = await fetchall(
                conn,
                f"""SELECT {_ACCOUNT_COLUMNS} FROM accounts a LEFT JOIN users u ON u.id = a.owner_id
                     ORDER BY a.account_type DESC, a.created_at, a.name""",
            )
    return {"accounts": rows}


@router.get("/directory")
async def account_directory(principal: Principal = Depends(get_current_user), db: Database = Depends(get_db)):
    """Payee list for transfers: active user accounts with owner names, but no balances."""
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT a.id, a.name, a.currency, u.full_name AS owner_name
                 FROM accounts a JOIN users u ON u.id = a.owner_id
                WHERE a.account_type = 'USER' AND a.status = 'ACTIVE'
                ORDER BY u.full_name, a.name""",
        )
    return {"accounts": rows}


@router.post("", status_code=201)
async def create_account(
    req: CreateAccountRequest,
    principal: Principal = Depends(require_roles("ADMIN")),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        async with conn.transaction():
            owner = await fetchone(conn, "SELECT id, role, is_active FROM users WHERE id = %s", [req.owner_id])
            if owner is None or owner["role"] != "ACCOUNT_HOLDER" or not owner["is_active"]:
                raise ApiError(422, errors.VALIDATION_ERROR, "Owner must be an active ACCOUNT_HOLDER.")
            row = await fetchone(
                conn,
                """INSERT INTO accounts (owner_id, name, account_type, currency)
                   VALUES (%s, %s, 'USER', %s) RETURNING id""",
                [req.owner_id, req.name, req.currency],
            )
            acct = await fetchone(
                conn,
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts a LEFT JOIN users u ON u.id = a.owner_id WHERE a.id = %s",
                [row["id"]],
            )
    return {"account": acct}


@router.get("/{account_id}")
async def get_account(
    account_id: UUID, request: Request,
    principal: Principal = Depends(get_current_user), db: Database = Depends(get_db),
):
    return {"account": await load_visible_account(db, principal, account_id, request)}


@router.post("/{account_id}/status")
async def set_account_status(
    account_id: UUID,
    req: AccountStatusRequest,
    principal: Principal = Depends(require_roles("ADMIN")),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        async with conn.transaction():
            acct = await fetchone(
                conn, "SELECT id, account_type FROM accounts WHERE id = %s FOR UPDATE", [account_id]
            )
            if acct is None:
                raise ApiError(404, errors.ACCOUNT_NOT_FOUND, "Account not found.")
            if acct["account_type"] == "SYSTEM":
                raise ApiError(422, errors.VALIDATION_ERROR, "The settlement account cannot be frozen.")
            await conn.execute("UPDATE accounts SET status = %s WHERE id = %s", [req.status, account_id])
            if req.status == "FROZEN":
                await conn.execute(
                    """INSERT INTO anomaly_flags (target_type, target_id, severity, reason, flagged_by)
                       VALUES ('ACCOUNT', %s, 'HIGH', %s, %s)""",
                    [account_id, f"Account frozen: {req.reason or 'no reason given'}", principal.id],
                )
            updated = await fetchone(
                conn,
                f"SELECT {_ACCOUNT_COLUMNS} FROM accounts a LEFT JOIN users u ON u.id = a.owner_id WHERE a.id = %s",
                [account_id],
            )
    return {"account": updated}


@router.get("/{account_id}/statement")
async def account_statement(
    account_id: UUID,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    before_seq: int | None = Query(None, ge=1, description="Return entries older than this account_seq"),
    principal: Principal = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    acct = await load_visible_account(db, principal, account_id, request)
    async with db.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            entries = await fetchall(
                conn,
                """SELECT e.id, e.transaction_id, e.account_seq, e.entry_type, e.amount, e.balance_after,
                          e.currency, e.entry_hash, e.created_at,
                          t.type AS transaction_type, t.description, t.external_ref,
                          c.account_id AS counterparty_account_id,
                          ca.name AS counterparty_account_name,
                          ca.account_type AS counterparty_account_type,
                          cu.full_name AS counterparty_owner_name
                     FROM ledger_entries e
                     JOIN transactions t ON t.id = e.transaction_id
                     LEFT JOIN ledger_entries c ON c.transaction_id = e.transaction_id AND c.account_id <> e.account_id
                     LEFT JOIN accounts ca ON ca.id = c.account_id
                     LEFT JOIN users cu ON cu.id = ca.owner_id
                    WHERE e.account_id = %s AND (%s::bigint IS NULL OR e.account_seq < %s::bigint)
                    ORDER BY e.account_seq DESC
                    LIMIT %s""",
                [account_id, before_seq, before_seq, limit],
            )
            summary = await fetchone(
                conn,
                """SELECT COUNT(*) AS entry_count,
                          COALESCE(SUM(amount) FILTER (WHERE entry_type = 'CREDIT'), 0)::bigint AS total_credits,
                          COALESCE(SUM(amount) FILTER (WHERE entry_type = 'DEBIT'), 0)::bigint  AS total_debits
                     FROM ledger_entries WHERE account_id = %s""",
                [account_id],
            )
    for e in entries:
        # Never leak the settlement account's running total to holders.
        if e["counterparty_account_type"] == "SYSTEM":
            e["counterparty_account_name"] = "External"
            e["counterparty_owner_name"] = None
    next_before = entries[-1]["account_seq"] if len(entries) == limit and entries[-1]["account_seq"] > 1 else None
    return {
        "account": acct,
        "summary": summary,
        "entries": entries,
        "next_before_seq": next_before,
    }
