"""Tamper resistance: the ledger is append-only and edits are detectable."""

import psycopg
import pytest
from psycopg import errors as pg_errors

from .conftest import app_role_conn, superuser_conn

MUTATIONS = [
    "UPDATE ledger_entries SET amount = amount + 1",
    "DELETE FROM ledger_entries",
    "TRUNCATE ledger_entries CASCADE",
    "UPDATE transactions SET amount = 1",
    "DELETE FROM transactions",
    "UPDATE refusal_audit_logs SET reason_code = 'NOTHING_TO_SEE'",
    "DELETE FROM refusal_audit_logs",
    "DELETE FROM accounts",
]


@pytest.mark.parametrize("sql", MUTATIONS)
async def test_app_role_has_no_privilege_to_mutate_history(h, sql):
    await h.deposit(h.alice, "a1", 1_000)
    await h.withdraw(h.alice, "a1", 5_000)  # creates a refusal row
    with app_role_conn() as conn:
        with pytest.raises(pg_errors.InsufficientPrivilege):
            conn.execute(sql)


@pytest.mark.parametrize("sql", MUTATIONS)
async def test_even_superuser_is_stopped_by_triggers(h, sql):
    await h.deposit(h.alice, "a1", 1_000)
    await h.withdraw(h.alice, "a1", 5_000)
    with superuser_conn() as conn:
        with pytest.raises(pg_errors.RaiseException):
            conn.execute(sql)


async def test_balance_cannot_change_without_a_ledger_entry(h):
    await h.deposit(h.alice, "a1", 1_000)
    with superuser_conn() as conn:
        conn.execute("UPDATE accounts SET balance = 1000000000 WHERE id = %s", [h.accounts["a1"]])
        with pytest.raises(pg_errors.RaiseException, match="does not match ledger balance"):
            conn.commit()  # deferred constraint trigger fires at COMMIT
    assert await h.balance("a1") == 1_000


async def test_unbalanced_transaction_cannot_commit(h):
    with superuser_conn() as conn:
        tx = conn.execute(
            """INSERT INTO transactions (type, amount, currency, initiated_by, idempotency_key)
               VALUES ('DEPOSIT', 500, 'INR', %s, 'forged') RETURNING id""",
            [h.alice.user_id],
        ).fetchone()
        conn.execute(
            """INSERT INTO ledger_entries (transaction_id, account_id, entry_type, amount, balance_after, currency)
               VALUES (%s, %s, 'CREDIT', 500, 500, 'INR')""",
            [tx["id"], h.accounts["a1"]],
        )
        conn.execute("UPDATE accounts SET balance = 500 WHERE id = %s", [h.accounts["a1"]])
        with pytest.raises(pg_errors.RaiseException, match="Unbalanced transaction"):
            conn.commit()
    assert await h.balance("a1") == 0


async def test_forged_running_balance_is_rejected(h):
    await h.deposit(h.alice, "a1", 1_000)
    with superuser_conn() as conn:
        tx = conn.execute(
            """INSERT INTO transactions (type, amount, currency, initiated_by, idempotency_key)
               VALUES ('TRANSFER', 10, 'INR', %s, 'forged-2') RETURNING id""",
            [h.alice.user_id],
        ).fetchone()
        with pytest.raises(pg_errors.RaiseException, match="breaks the running balance"):
            conn.execute(
                """INSERT INTO ledger_entries (transaction_id, account_id, entry_type, amount, balance_after, currency)
                   VALUES (%s, %s, 'DEBIT', 10, 999999, 'INR')""",
                [tx["id"], h.accounts["a1"]],
            )


async def test_negative_user_balance_violates_check_constraint(h):
    with superuser_conn() as conn:
        with pytest.raises(pg_errors.CheckViolation, match="accounts_balance_non_negative"):
            conn.execute("UPDATE accounts SET balance = -1 WHERE id = %s", [h.accounts["a1"]])


async def test_completed_idempotency_record_is_frozen(h):
    await h.deposit(h.alice, "a1", 1_000, key="frozen-key")
    with superuser_conn() as conn:
        with pytest.raises(pg_errors.RaiseException, match="immutable"):
            conn.execute(
                "UPDATE idempotency_keys SET response_code = 500 WHERE idempotency_key = 'frozen-key'"
            )


async def test_hash_chain_detects_edits_made_with_triggers_disabled(h):
    """A DBA who bypasses every trigger can still be caught: the hash chain breaks."""
    await h.deposit(h.alice, "a1", 10_000)
    await h.withdraw(h.alice, "a1", 2_500)
    await h.assert_invariants()

    with superuser_conn() as conn:
        conn.execute("SET session_replication_role = replica")  # disables ordinary triggers
        conn.execute(
            """UPDATE ledger_entries SET amount = 1, balance_after = 9999
                WHERE account_id = %s AND account_seq = 2""",
            [h.accounts["a1"]],
        )
        conn.commit()

    report = await h.verify()
    by_name = {c["name"]: c for c in report["checks"]}
    assert report["passed"] is False
    assert by_name["hash_chain_intact"]["passed"] is False
    broken = by_name["hash_chain_intact"]["details"]["broken_links"]
    assert broken and broken[0]["account_id"] == h.accounts["a1"] and broken[0]["account_seq"] == 2
    assert by_name["double_entry_balanced"]["passed"] is False
    assert by_name["cached_balance_matches_ledger"]["passed"] is False
    # The forger kept the running total self-consistent (10000 - 1 = 9999), so this check alone
    # would be fooled — which is exactly why the hash chain exists.
    assert by_name["running_balance_continuity"]["passed"] is True
