"""Hostile payloads: amounts must be positive integers in minor units."""

import pytest

from .conftest import count


@pytest.mark.parametrize(
    "amount",
    [-1, 0, 1.5, 100.0, "100", True, None, [100], 10**13, 2**70, "NaN", float("1e3")],
)
async def test_bad_amounts_are_refused_and_audited(h, amount):
    await h.deposit(h.alice, "a1", 10_000)
    r = await h.post(h.alice, "/v1/withdrawals", {"account_id": h.accounts["a1"], "amount": amount})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await h.balance("a1") == 10_000
    assert count(
        "SELECT count(*) AS n FROM refusal_audit_logs WHERE reason_code = 'VALIDATION_ERROR' AND attempted_by = %s",
        [h.alice.user_id],
    ) == 1


async def test_unknown_fields_are_rejected(h):
    r = await h.post(h.alice, "/v1/deposits", {"account_id": h.accounts["a1"], "amount": 100, "balance": 10**9})
    assert r.status_code == 422


async def test_transfer_to_same_account_is_refused(h):
    await h.deposit(h.alice, "a1", 1_000)
    r = await h.transfer(h.alice, "a1", "a1", 100)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SAME_ACCOUNT"


async def test_transfer_to_unknown_or_system_account_is_refused(h):
    await h.deposit(h.alice, "a1", 1_000)
    unknown = await h.post(
        h.alice, "/v1/transfers",
        {"source_account_id": h.accounts["a1"], "destination_account_id": "00000000-0000-0000-0000-000000000000",
         "amount": 100},
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"

    settlement = str(h.app.state.db.settlement_account_id)
    to_system = await h.post(
        h.alice, "/v1/transfers",
        {"source_account_id": h.accounts["a1"], "destination_account_id": settlement, "amount": 100},
    )
    assert to_system.status_code == 404
    assert await h.balance("a1") == 1_000


async def test_currency_mismatch_is_refused(h):
    r = await h.post(h.alice, "/v1/deposits", {"account_id": h.accounts["a1"], "amount": 100, "currency": "USD"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CURRENCY_MISMATCH"


async def test_overdraft_by_one_paisa_is_refused(h):
    await h.deposit(h.alice, "a1", 1_000)
    r = await h.withdraw(h.alice, "a1", 1_001)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "INSUFFICIENT_FUNDS"
    assert body["details"] == {"available": 1_000, "requested": 1_001}
    exact = await h.withdraw(h.alice, "a1", 1_000)
    assert exact.status_code == 201
    assert await h.balance("a1") == 0
