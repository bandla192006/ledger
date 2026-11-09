"""Idempotency: retries and racing duplicates must post exactly once."""

import asyncio

from .conftest import count


def tx_count() -> int:
    return count("SELECT count(*) AS n FROM transactions")


async def test_retry_returns_cached_response_without_reexecuting(h):
    first = await h.deposit(h.alice, "a1", 12_345, key="dep-1")
    before = tx_count()
    second = await h.deposit(h.alice, "a1", 12_345, key="dep-1")

    assert first.status_code == second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert first.headers.get("Idempotent-Replayed") is None
    assert second.json() == first.json()
    assert tx_count() == before
    assert await h.balance("a1") == 12_345


async def test_racing_duplicates_post_exactly_once(h):
    await h.deposit(h.alice, "a1", 100_000)
    before = tx_count()

    responses = await asyncio.gather(*(h.transfer(h.alice, "a1", "b1", 1_000, key="same-key") for _ in range(25)))
    codes = [r.status_code for r in responses]

    assert set(codes) <= {201, 409}, codes
    assert tx_count() == before + 1
    tx_ids = {r.json()["transaction"]["id"] for r in responses if r.status_code == 201}
    assert len(tx_ids) == 1  # every success is the same transaction (original or replay)
    conflicts = [r.json()["error"]["code"] for r in responses if r.status_code == 409]
    assert all(c == "CONCURRENT_CONFLICT" for c in conflicts)
    assert await h.balance("a1") == 99_000

    # Once the first request committed, a retry is a clean replay.
    retry = await h.transfer(h.alice, "a1", "b1", 1_000, key="same-key")
    assert retry.status_code == 201 and retry.headers.get("Idempotent-Replayed") == "true"
    await h.assert_invariants()


async def test_same_key_different_payload_is_rejected(h):
    await h.deposit(h.alice, "a1", 50_000)
    assert (await h.withdraw(h.alice, "a1", 100, key="w-1")).status_code == 201

    changed_amount = await h.withdraw(h.alice, "a1", 999, key="w-1")
    assert changed_amount.status_code == 422
    assert changed_amount.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    other_endpoint = await h.deposit(h.alice, "a1", 100, key="w-1")
    assert other_endpoint.status_code == 422
    assert other_endpoint.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    assert await h.balance("a1") == 49_900
    assert count("SELECT count(*) AS n FROM refusal_audit_logs WHERE reason_code = 'IDEMPOTENCY_KEY_REUSED'") == 2


async def test_refusals_are_cached_too(h):
    """A retry gets the same answer as the original, even if the world changed in between."""
    refused = await h.withdraw(h.alice, "a1", 500, key="early")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "INSUFFICIENT_FUNDS"

    await h.deposit(h.alice, "a1", 10_000)
    retry = await h.withdraw(h.alice, "a1", 500, key="early")
    assert retry.status_code == 422
    assert retry.headers.get("Idempotent-Replayed") == "true"
    assert await h.balance("a1") == 10_000


async def test_keys_are_scoped_per_user(h):
    a = await h.deposit(h.alice, "a1", 1_000, key="shared-key")
    b = await h.deposit(h.bob, "b1", 2_000, key="shared-key")
    assert a.status_code == b.status_code == 201
    assert a.json()["transaction"]["id"] != b.json()["transaction"]["id"]
    assert b.headers.get("Idempotent-Replayed") is None


async def test_missing_or_malformed_key_is_rejected(h):
    missing = await h.deposit(h.alice, "a1", 1_000, key=None)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"

    bad = await h.deposit(h.alice, "a1", 1_000, key="has spaces; and <script>")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"

    assert await h.balance("a1") == 0
