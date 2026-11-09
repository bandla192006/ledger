"""Invariant proofs and statements."""

from .conftest import count


async def test_seeded_ledger_satisfies_every_invariant(h):
    report = await h.assert_invariants()
    names = {c["name"] for c in report["checks"]}
    assert names == {
        "double_entry_balanced",
        "global_debits_equal_credits",
        "zero_sum_balances",
        "external_flow_reconciles",
        "internal_transfers_net_zero",
        "no_negative_user_balances",
        "cached_balance_matches_ledger",
        "running_balance_continuity",
        "hash_chain_intact",
        "idempotency_consistent",
    }
    assert count("SELECT count(*) AS n FROM invariant_runs WHERE passed") == 1


async def test_external_flow_equals_deposits_minus_withdrawals(h):
    await h.deposit(h.alice, "a1", 70_000)
    await h.deposit(h.bob, "b1", 30_000)
    await h.withdraw(h.alice, "a1", 15_000)
    await h.transfer(h.bob, "b1", "a2", 10_000)

    report = await h.assert_invariants()
    flow = next(c for c in report["checks"] if c["name"] == "external_flow_reconciles")["details"]
    assert flow["user_balance_total"] == flow["total_deposits"] - flow["total_withdrawals"]
    assert flow["settlement_balance"] == -flow["user_balance_total"]


async def test_statement_shows_running_balance_and_paginates(h):
    await h.deposit(h.alice, "a1", 10_000)
    for _ in range(5):
        await h.withdraw(h.alice, "a1", 1_000)
    await h.transfer(h.alice, "a1", "b1", 500)

    r = await h.client.get(f"/v1/accounts/{h.accounts['a1']}/statement?limit=4", headers=h.alice.headers)
    page = r.json()
    assert [e["account_seq"] for e in page["entries"]] == [7, 6, 5, 4]
    assert page["entries"][0]["balance_after"] == 4_500
    assert page["entries"][0]["counterparty_owner_name"] == "Bob Mehta"
    assert page["entries"][1]["counterparty_account_name"] == "External"
    assert page["summary"] == {"entry_count": 7, "total_credits": 10_000, "total_debits": 5_500}

    r = await h.client.get(
        f"/v1/accounts/{h.accounts['a1']}/statement?limit=4&before_seq={page['next_before_seq']}",
        headers=h.alice.headers,
    )
    assert [e["account_seq"] for e in r.json()["entries"]] == [3, 2, 1]
    assert r.json()["next_before_seq"] is None
