"""Posting engine: deposits, withdrawals and transfers.

Every handler runs inside the idempotency transaction and follows the same shape:

  1. Lock every account it will touch with ONE statement:
         SELECT ... FROM accounts WHERE id = ANY(...) ORDER BY id FOR UPDATE
     Locks are always acquired in ascending id order, so two transfers A->B and
     B->A can never deadlock, and a second concurrent withdrawal waits for the
     first to commit and then reads the *new* balance.
  2. Validate against the locked (therefore current) rows. Refusals raise
     `Refused`, which the idempotency engine audits and caches.
  3. Write one transactions row + one ledger entry per leg + balance updates.
     The DB re-checks everything: CHECK (balance >= 0), running balance,
     double-entry balance and ledger/balance agreement at COMMIT.
"""

from uuid import UUID

from psycopg import AsyncConnection, errors as pg_errors

from . import errors
from .db import fetchall, fetchone
from .errors import Refused
from .idempotency import Outcome
from .schemas import DepositRequest, TransferRequest, WithdrawalRequest
from .security import Principal

# Tests flip this off to prove the database alone still blocks overdrafts.
ENFORCE_APP_FUNDS_CHECK = True

_LOCK_SQL = """
SELECT id, owner_id, name, account_type, system_code, currency, balance, status
  FROM accounts
 WHERE id = ANY(%s)
 ORDER BY id
   FOR UPDATE
"""


async def lock_accounts(conn: AsyncConnection, account_ids: list[UUID]) -> dict[UUID, dict]:
    rows = await fetchall(conn, _LOCK_SQL, [sorted(set(account_ids))])
    return {row["id"]: row for row in rows}


def _require_owned_active(acct: dict | None, principal: Principal, account_id: UUID, amount: int,
                          *, as_source: bool, currency: str) -> dict:
    if acct is None or acct["account_type"] != "USER" or acct["owner_id"] != principal.id:
        # Same answer whether the account is someone else's or doesn't exist: no enumeration.
        raise Refused(
            403, errors.UNAUTHORIZED, "You can only operate on your own accounts.",
            source_account_id=account_id if as_source else None,
            destination_account_id=None if as_source else account_id,
            requested_amount=amount,
        )
    if acct["status"] != "ACTIVE":
        raise Refused(
            422, errors.ACCOUNT_FROZEN, "This account is frozen.",
            source_account_id=account_id if as_source else None,
            destination_account_id=None if as_source else account_id,
            requested_amount=amount, balance_snapshot=acct["balance"],
        )
    if acct["currency"].strip() != currency:
        raise Refused(
            422, errors.CURRENCY_MISMATCH,
            f"Account currency is {acct['currency']}, request currency is {currency}.",
            source_account_id=account_id if as_source else None,
            destination_account_id=None if as_source else account_id,
            requested_amount=amount, balance_snapshot=acct["balance"],
        )
    return acct


def _require_funds(acct: dict, amount: int, *, destination_account_id: UUID | None = None) -> None:
    if ENFORCE_APP_FUNDS_CHECK and acct["balance"] < amount:
        raise Refused(
            422, errors.INSUFFICIENT_FUNDS, "Insufficient funds.",
            details={"available": acct["balance"], "requested": amount},
            source_account_id=acct["id"], destination_account_id=destination_account_id,
            requested_amount=amount, balance_snapshot=acct["balance"],
        )


async def _post(
    conn: AsyncConnection,
    *,
    principal: Principal,
    tx_type: str,
    amount: int,
    currency: str,
    debit: dict,
    credit: dict,
    idempotency_key: str,
    description: str | None,
    external_ref: str | None,
) -> Outcome:
    try:
        async with conn.transaction():  # SAVEPOINT: a DB refusal leaves the outer transaction usable
            tx = await fetchone(
                conn,
                """INSERT INTO transactions
                       (type, amount, currency, initiated_by, idempotency_key, description, external_ref)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, type, amount, currency, initiated_by, idempotency_key,
                             description, external_ref, created_at""",
                [tx_type, amount, currency, principal.id, idempotency_key, description, external_ref],
            )
            legs = sorted([(debit, "DEBIT"), (credit, "CREDIT")], key=lambda leg: leg[0]["id"])
            entries = []
            for acct, side in legs:
                new_balance = acct["balance"] + (amount if side == "CREDIT" else -amount)
                # account_seq, prev_hash, entry_hash and created_at are assigned by the DB trigger.
                entry = await fetchone(
                    conn,
                    """INSERT INTO ledger_entries
                           (transaction_id, account_id, entry_type, amount, balance_after, currency)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       RETURNING id, account_id, account_seq, entry_type, amount, balance_after,
                                 currency, entry_hash, created_at""",
                    [tx["id"], acct["id"], side, amount, new_balance, currency],
                )
                await conn.execute("UPDATE accounts SET balance = %s WHERE id = %s", [new_balance, acct["id"]])
                acct["balance"] = new_balance
                entries.append(entry)
    except pg_errors.CheckViolation as exc:
        if exc.diag.constraint_name == "accounts_balance_non_negative":
            raise Refused(
                422, errors.INSUFFICIENT_FUNDS, "Insufficient funds.",
                details={"enforced_by": "database_check_constraint"},
                source_account_id=debit["id"], destination_account_id=credit["id"],
                requested_amount=amount, balance_snapshot=debit["balance"],
            ) from exc
        raise

    # Only reveal legs on the caller's own accounts (never the counterparty's balance).
    visible = [e for e in entries if e["account_id"] in {debit.get("_visible_id"), credit.get("_visible_id")}]
    return Outcome(
        201,
        {"transaction": {**tx, "entries": visible}},
        transaction_id=tx["id"],
    )


async def deposit(conn: AsyncConnection, principal: Principal, req: DepositRequest, key: str,
                  settlement_id: UUID) -> Outcome:
    accounts = await lock_accounts(conn, [req.account_id, settlement_id])
    acct = _require_owned_active(accounts.get(req.account_id), principal, req.account_id, req.amount,
                                 as_source=False, currency=req.currency)
    settlement = accounts[settlement_id]
    acct["_visible_id"] = acct["id"]
    # External money in: DEBIT settlement/clearing, CREDIT the user.
    return await _post(conn, principal=principal, tx_type="DEPOSIT", amount=req.amount, currency=req.currency,
                       debit=settlement, credit=acct, idempotency_key=key,
                       description=req.description, external_ref=req.external_ref)


async def withdraw(conn: AsyncConnection, principal: Principal, req: WithdrawalRequest, key: str,
                   settlement_id: UUID) -> Outcome:
    accounts = await lock_accounts(conn, [req.account_id, settlement_id])
    acct = _require_owned_active(accounts.get(req.account_id), principal, req.account_id, req.amount,
                                 as_source=True, currency=req.currency)
    _require_funds(acct, req.amount)
    settlement = accounts[settlement_id]
    acct["_visible_id"] = acct["id"]
    # Money out: DEBIT the user, CREDIT settlement/clearing.
    return await _post(conn, principal=principal, tx_type="WITHDRAWAL", amount=req.amount, currency=req.currency,
                       debit=acct, credit=settlement, idempotency_key=key,
                       description=req.description, external_ref=req.external_ref)


async def transfer(conn: AsyncConnection, principal: Principal, req: TransferRequest, key: str) -> Outcome:
    if req.source_account_id == req.destination_account_id:
        raise Refused(
            422, errors.SAME_ACCOUNT, "Source and destination must differ.",
            source_account_id=req.source_account_id, destination_account_id=req.destination_account_id,
            requested_amount=req.amount,
        )
    accounts = await lock_accounts(conn, [req.source_account_id, req.destination_account_id])
    source = _require_owned_active(accounts.get(req.source_account_id), principal, req.source_account_id,
                                   req.amount, as_source=True, currency=req.currency)

    dest = accounts.get(req.destination_account_id)
    if dest is None or dest["account_type"] != "USER":
        raise Refused(
            404, errors.ACCOUNT_NOT_FOUND, "Destination account not found.",
            source_account_id=source["id"], destination_account_id=req.destination_account_id,
            requested_amount=req.amount, balance_snapshot=source["balance"],
        )
    if dest["status"] != "ACTIVE":
        raise Refused(
            422, errors.ACCOUNT_FROZEN, "Destination account is frozen.",
            source_account_id=source["id"], destination_account_id=dest["id"],
            requested_amount=req.amount, balance_snapshot=source["balance"],
        )
    if dest["currency"].strip() != req.currency:
        raise Refused(
            422, errors.CURRENCY_MISMATCH, "Destination account uses a different currency.",
            source_account_id=source["id"], destination_account_id=dest["id"],
            requested_amount=req.amount, balance_snapshot=source["balance"],
        )
    _require_funds(source, req.amount, destination_account_id=dest["id"])

    source["_visible_id"] = source["id"]
    if dest["owner_id"] == principal.id:  # transfer between my own accounts: show both legs
        dest["_visible_id"] = dest["id"]
    return await _post(conn, principal=principal, tx_type="TRANSFER", amount=req.amount, currency=req.currency,
                       debit=source, credit=dest, idempotency_key=key,
                       description=req.description, external_ref=None)
