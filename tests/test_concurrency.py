"""Adversarial concurrency: racing requests must never double-spend or deadlock."""

import asyncio
import random

from app import money

from .conftest import count


async def test_racing_withdrawals_never_double_spend(h):
    assert (await h.deposit(h.alice, "a1", 10_000)).status_code == 201

    # 50 simultaneous withdrawals of the full balance, each with its own idempotency key.
    responses = await asyncio.gather(*(h.withdraw(h.alice, "a1", 10_000) for _ in range(50)))
    codes = [r.status_code for r in responses]

    assert codes.count(201) == 1, codes
    assert codes.count(422) == 49, codes
    assert all(r.json()["error"]["code"] == "INSUFFICIENT_FUNDS" for r in responses if r.status_code == 422)
    assert await h.balance("a1") == 0
    assert count(
        "SELECT count(*) AS n FROM refusal_audit_logs WHERE reason_code = 'INSUFFICIENT_FUNDS' AND source_account_id = %s",
        [h.accounts["a1"]],
    ) == 49
    await h.assert_invariants()


async def test_racing_transfers_drain_exactly_to_the_limit(h):
    await h.deposit(h.alice, "a1", 100_000)

    responses = await asyncio.gather(*(h.transfer(h.alice, "a1", "b1", 7_000) for _ in range(40)))
    successes = sum(r.status_code == 201 for r in responses)

    assert successes == 100_000 // 7_000  # 14, never 15
    assert await h.balance("a1") == 100_000 - successes * 7_000
    assert await h.balance("b1") == successes * 7_000
    await h.assert_invariants()


async def test_opposing_transfers_do_not_deadlock(h):
    await h.deposit(h.alice, "a1", 1_000_000)
    await h.deposit(h.bob, "b1", 1_000_000)
    rng = random.Random(7)

    calls = []
    for i in range(120):
        amount = rng.randint(1, 5_000)
        if i % 2 == 0:
            calls.append(h.transfer(h.alice, "a1", "b1", amount))
        else:
            calls.append(h.transfer(h.bob, "b1", "a1", amount))
    responses = await asyncio.gather(*calls)

    assert all(r.status_code == 201 for r in responses), {r.status_code for r in responses}
    assert await h.balance("a1") + await h.balance("b1") == 2_000_000  # nothing created or lost
    await h.assert_invariants()


async def test_database_alone_blocks_overdraft_when_app_check_is_disabled(h, monkeypatch):
    """Defence in depth: switch the application-level funds check off and race anyway."""
    await h.deposit(h.alice, "a1", 5_000)
    monkeypatch.setattr(money, "ENFORCE_APP_FUNDS_CHECK", False)

    responses = await asyncio.gather(*(h.withdraw(h.alice, "a1", 5_000) for _ in range(20)))
    codes = [r.status_code for r in responses]

    assert codes.count(201) == 1, codes
    refused = [r.json()["error"] for r in responses if r.status_code == 422]
    assert len(refused) == 19
    assert all(e["code"] == "INSUFFICIENT_FUNDS" for e in refused)
    assert all(e["details"]["enforced_by"] == "database_check_constraint" for e in refused)
    assert await h.balance("a1") == 0
    await h.assert_invariants()


async def test_chaotic_mixed_workload_preserves_every_invariant(h):
    rng = random.Random(42)
    await h.deposit(h.alice, "a1", 50_000)
    await h.deposit(h.bob, "b1", 50_000)

    def random_op():
        roll = rng.random()
        amount = rng.choice([1, 99, 1_000, 5_000, 20_000, 80_000])
        if roll < 0.2:
            return h.deposit(rng.choice([h.alice]), rng.choice(["a1", "a2"]), amount)
        if roll < 0.4:
            return h.withdraw(h.alice, rng.choice(["a1", "a2"]), amount)
        if roll < 0.5:
            return h.withdraw(h.bob, "b1", amount)
        if roll < 0.75:
            src, dst = rng.sample(["a1", "a2", "b1"], 2) if rng.random() < 0.5 else ("a1", "a2")
            actor = h.bob if src == "b1" else h.alice
            return h.transfer(actor, src, dst, amount)
        return h.transfer(h.bob, "b1", rng.choice(["a1", "a2"]), amount)

    responses = await asyncio.gather(*(random_op() for _ in range(300)))
    codes = {r.status_code for r in responses}
    assert codes <= {201, 422}, codes  # only success or a clean, audited refusal

    for acct in ("a1", "a2", "b1"):
        assert await h.balance(acct) >= 0
    report = await h.assert_invariants()
    assert report["totals"]["transactions"] >= 3
