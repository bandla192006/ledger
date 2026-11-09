"""Invariant verification ("proofs") over a single consistent snapshot.

Runs in a REPEATABLE READ, READ ONLY transaction, so every check sees the same
committed state even while money keeps moving.
"""

from typing import Any

from psycopg import AsyncConnection

from .db import fetchall, fetchone
from .hashing import GENESIS_HASH, ledger_entry_hash


def _check(name: str, description: str, passed: bool, **details: Any) -> dict:
    return {"name": name, "description": description, "passed": passed, "details": details}


async def run_invariant_checks(conn: AsyncConnection) -> dict:
    async with conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        checks = [
            await _double_entry(conn),
            await _global_debits_credits(conn),
            await _zero_sum(conn),
            await _external_flow(conn),
            await _internal_transfers(conn),
            await _no_negative(conn),
            await _cached_balances(conn),
            await _running_balances(conn),
            await _hash_chain(conn),
            await _idempotency(conn),
        ]
        totals = await fetchone(
            conn,
            """SELECT (SELECT count(*) FROM transactions)                         AS transactions,
                      (SELECT count(*) FROM ledger_entries)                       AS ledger_entries,
                      (SELECT count(*) FROM accounts WHERE account_type = 'USER') AS user_accounts,
                      (SELECT count(*) FROM refusal_audit_logs)                   AS refusals""",
        )
    return {"passed": all(c["passed"] for c in checks), "checks": checks, "totals": totals}


async def _double_entry(conn) -> dict:
    bad = await fetchall(
        conn,
        """SELECT t.id, t.amount,
                  COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'DEBIT'), 0)::bigint  AS debits,
                  COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'CREDIT'), 0)::bigint AS credits,
                  COUNT(e.id) AS legs
             FROM transactions t
             LEFT JOIN ledger_entries e ON e.transaction_id = t.id
            GROUP BY t.id, t.amount
           HAVING COUNT(e.id) < 2
               OR COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'DEBIT'), 0)
                  <> COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'CREDIT'), 0)
               OR COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'DEBIT'), 0) <> t.amount
            LIMIT 20""",
    )
    orphans = await fetchone(
        conn,
        """SELECT count(*) AS n FROM ledger_entries e
            WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.id = e.transaction_id)""",
    )
    return _check(
        "double_entry_balanced",
        "Every transaction has >= 2 legs and sum(DEBIT) = sum(CREDIT) = transaction amount.",
        not bad and orphans["n"] == 0,
        unbalanced_transactions=bad,
        orphan_entries=orphans["n"],
    )


async def _global_debits_credits(conn) -> dict:
    row = await fetchone(
        conn,
        """SELECT COALESCE(SUM(amount) FILTER (WHERE entry_type = 'DEBIT'), 0)  AS debits,
                  COALESCE(SUM(amount) FILTER (WHERE entry_type = 'CREDIT'), 0) AS credits
             FROM ledger_entries""",
    )
    return _check(
        "global_debits_equal_credits",
        "Across the whole ledger, total debits equal total credits.",
        row["debits"] == row["credits"],
        total_debits=int(row["debits"]),
        total_credits=int(row["credits"]),
    )


async def _zero_sum(conn) -> dict:
    row = await fetchone(conn, "SELECT COALESCE(SUM(balance), 0) AS total FROM accounts")
    return _check(
        "zero_sum_balances",
        "Sum of every account balance (users + settlement) is exactly 0: no money created or destroyed.",
        row["total"] == 0,
        sum_of_all_balances=int(row["total"]),
    )


async def _external_flow(conn) -> dict:
    row = await fetchone(
        conn,
        """SELECT (SELECT COALESCE(SUM(balance), 0) FROM accounts WHERE account_type = 'USER') AS user_total,
                  (SELECT balance FROM accounts WHERE system_code = 'SETTLEMENT')             AS settlement,
                  (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'DEPOSIT')    AS deposits,
                  (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'WITHDRAWAL') AS withdrawals""",
    )
    net = row["deposits"] - row["withdrawals"]
    return _check(
        "external_flow_reconciles",
        "Sum of user balances = total deposits - total withdrawals = -(settlement balance).",
        row["user_total"] == net and row["settlement"] == -net,
        user_balance_total=int(row["user_total"]),
        total_deposits=int(row["deposits"]),
        total_withdrawals=int(row["withdrawals"]),
        settlement_balance=int(row["settlement"]),
    )


async def _internal_transfers(conn) -> dict:
    row = await fetchone(
        conn,
        """SELECT COALESCE(SUM(CASE WHEN e.entry_type = 'CREDIT' THEN e.amount ELSE -e.amount END), 0) AS net,
                  COUNT(*) FILTER (WHERE a.account_type <> 'USER') AS system_legs,
                  COUNT(DISTINCT t.id) AS transfers
             FROM transactions t
             JOIN ledger_entries e ON e.transaction_id = t.id
             JOIN accounts a ON a.id = e.account_id
            WHERE t.type = 'TRANSFER'""",
    )
    return _check(
        "internal_transfers_net_zero",
        "Internal transfers only touch user accounts and net to exactly 0.",
        row["net"] == 0 and row["system_legs"] == 0,
        net=int(row["net"]),
        transfers=row["transfers"],
        legs_on_system_accounts=row["system_legs"],
    )


async def _no_negative(conn) -> dict:
    rows = await fetchall(
        conn,
        "SELECT id, name, balance FROM accounts WHERE account_type = 'USER' AND balance < 0 LIMIT 20",
    )
    return _check(
        "no_negative_user_balances",
        "No user account has a negative balance (no unauthorised overdraft).",
        not rows,
        negative_accounts=rows,
    )


async def _cached_balances(conn) -> dict:
    rows = await fetchall(
        conn,
        """SELECT a.id, a.name, a.balance,
                  COALESCE(SUM(CASE WHEN e.entry_type = 'CREDIT' THEN e.amount ELSE -e.amount END), 0)::bigint AS ledger
             FROM accounts a
             LEFT JOIN ledger_entries e ON e.account_id = a.id
            GROUP BY a.id
           HAVING a.balance <> COALESCE(SUM(CASE WHEN e.entry_type = 'CREDIT' THEN e.amount ELSE -e.amount END), 0)
            LIMIT 20""",
    )
    return _check(
        "cached_balance_matches_ledger",
        "Each account's stored balance equals sum(CREDIT) - sum(DEBIT) of its ledger entries.",
        not rows,
        mismatched_accounts=rows,
    )


async def _running_balances(conn) -> dict:
    rows = await fetchall(
        conn,
        """SELECT account_id, account_seq, balance_after, expected, rn FROM (
               SELECT account_id, account_seq, balance_after,
                      COALESCE(LAG(balance_after) OVER w, 0)
                        + CASE WHEN entry_type = 'CREDIT' THEN amount ELSE -amount END AS expected,
                      ROW_NUMBER() OVER w AS rn
                 FROM ledger_entries
               WINDOW w AS (PARTITION BY account_id ORDER BY account_seq)
           ) s
           WHERE balance_after <> expected OR account_seq <> rn
           LIMIT 20""",
    )
    return _check(
        "running_balance_continuity",
        "Per account, entries are numbered 1..n without gaps and each balance_after = previous + signed amount.",
        not rows,
        broken_entries=rows,
    )


async def _hash_chain(conn) -> dict:
    broken: list[dict] = []
    checked = 0
    prev_account = None
    prev_hash = GENESIS_HASH
    cur = conn.cursor(name="hash_chain_scan")  # server-side cursor: constant memory
    await cur.execute(
        """SELECT id, transaction_id, account_id, account_seq, entry_type, amount, balance_after,
                  currency, prev_hash, entry_hash, created_at
             FROM ledger_entries ORDER BY account_id, account_seq"""
    )
    async for e in cur:
        if e["account_id"] != prev_account:
            prev_account, prev_hash = e["account_id"], GENESIS_HASH
        recomputed = ledger_entry_hash(
            prev_hash, e["id"], e["transaction_id"], e["account_id"], e["account_seq"],
            e["entry_type"], e["amount"], e["balance_after"], e["currency"], e["created_at"],
        )
        if e["prev_hash"] != prev_hash or e["entry_hash"] != recomputed:
            if len(broken) < 20:
                broken.append({"entry_id": e["id"], "account_id": e["account_id"], "account_seq": e["account_seq"],
                               "stored_hash": e["entry_hash"], "recomputed_hash": recomputed})
        prev_hash = e["entry_hash"]
        checked += 1
    await cur.close()
    return _check(
        "hash_chain_intact",
        "Every entry's SHA-256 hash, recomputed independently in Python, matches and links to its predecessor.",
        not broken,
        entries_checked=checked,
        broken_links=broken,
    )


async def _idempotency(conn) -> dict:
    missing = await fetchone(
        conn,
        """SELECT count(*) AS n FROM transactions t
            WHERE NOT EXISTS (
                SELECT 1 FROM idempotency_keys k
                 WHERE k.user_id = t.initiated_by AND k.idempotency_key = t.idempotency_key
                   AND k.status = 'COMPLETED' AND k.transaction_id = t.id)""",
    )
    dangling = await fetchone(
        conn,
        """SELECT count(*) AS n FROM idempotency_keys
            WHERE status = 'COMPLETED' AND response_code BETWEEN 200 AND 299 AND transaction_id IS NULL""",
    )
    in_flight = await fetchone(conn, "SELECT count(*) AS n FROM idempotency_keys WHERE status = 'IN_FLIGHT'")
    return _check(
        "idempotency_consistent",
        "Every transaction maps to exactly one COMPLETED idempotency key; no successful key lacks a transaction; "
        "no key is stuck IN_FLIGHT.",
        missing["n"] == 0 and dangling["n"] == 0 and in_flight["n"] == 0,
        transactions_without_key=missing["n"],
        successful_keys_without_transaction=dangling["n"],
        stuck_in_flight=in_flight["n"],
    )
