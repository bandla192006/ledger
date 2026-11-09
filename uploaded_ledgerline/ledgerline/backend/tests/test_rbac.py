"""Role-based access control and data scoping."""

import pytest

from .conftest import count


async def test_holder_cannot_withdraw_from_someone_elses_account(h):
    await h.deposit(h.bob, "b1", 10_000)
    r = await h.withdraw(h.alice, "b1", 5_000)

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert await h.balance("b1") == 10_000
    assert count(
        """SELECT count(*) AS n FROM refusal_audit_logs
            WHERE reason_code = 'UNAUTHORIZED' AND attempted_by = %s AND source_account_id = %s
              AND requested_amount = 5000""",
        [h.alice.user_id, h.accounts["b1"]],
    ) == 1


async def test_holder_cannot_transfer_out_of_someone_elses_account(h):
    await h.deposit(h.bob, "b1", 10_000)
    r = await h.transfer(h.alice, "b1", "a1", 10_000)
    assert r.status_code == 403
    assert await h.balance("b1") == 10_000
    assert await h.balance("a1") == 0


async def test_holder_cannot_deposit_into_someone_elses_account(h):
    r = await h.deposit(h.alice, "b1", 10_000)
    assert r.status_code == 403
    assert await h.balance("b1") == 0


@pytest.mark.parametrize("role", ["admin", "auditor"])
async def test_admins_and_auditors_cannot_move_money(h, role):
    actor = h.actors[role]
    for call in (
        h.deposit(actor, "a1", 100),
        h.withdraw(actor, "a1", 100),
        h.transfer(actor, "a1", "b1", 100),
    ):
        r = await call
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "FORBIDDEN_ROLE"
    assert count("SELECT count(*) AS n FROM refusal_audit_logs WHERE reason_code = 'FORBIDDEN_ROLE'") == 3


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/ledger/entries"),
        ("GET", "/v1/audit/refusals"),
        ("GET", "/v1/audit/idempotency-keys"),
        ("POST", "/v1/invariants/verify"),
        ("GET", "/v1/users"),
        ("GET", "/v1/anomalies"),
    ],
)
async def test_holder_cannot_read_global_views(h, method, path):
    r = await h.client.request(method, path, headers=h.alice.headers)
    assert r.status_code == 403


async def test_auditor_is_read_only(h):
    for method, path, body in (
        ("POST", "/v1/accounts", {"owner_id": h.alice.user_id, "name": "x"}),
        ("POST", f"/v1/accounts/{h.accounts['a1']}/status", {"status": "FROZEN"}),
        ("POST", "/v1/anomalies", {"target_type": "ACCOUNT", "target_id": h.accounts["a1"], "reason": "x"}),
        ("POST", "/v1/users", {"email": "e@x.io", "full_name": "E", "role": "ADMIN", "password": "12345678"}),
    ):
        r = await h.client.request(method, path, json=body, headers=h.auditor.headers)
        assert r.status_code == 403, path
    for path in ("/v1/ledger/entries", "/v1/audit/refusals", "/v1/audit/idempotency-keys", "/v1/accounts"):
        assert (await h.client.get(path, headers=h.auditor.headers)).status_code == 200
    assert (await h.client.post("/v1/invariants/verify", headers=h.auditor.headers)).status_code == 200


async def test_holder_sees_only_own_accounts_and_statements(h):
    r = await h.client.get("/v1/accounts", headers=h.alice.headers)
    owners = {a["owner_id"] for a in r.json()["accounts"]}
    assert owners == {h.alice.user_id}

    r = await h.client.get(f"/v1/accounts/{h.accounts['b1']}/statement", headers=h.alice.headers)
    assert r.status_code == 403
    r = await h.client.get(f"/v1/accounts/{h.accounts['a1']}/statement", headers=h.alice.headers)
    assert r.status_code == 200


async def test_transfer_response_does_not_leak_counterparty_balance(h):
    await h.deposit(h.alice, "a1", 10_000)
    await h.deposit(h.bob, "b1", 777_777)
    r = await h.transfer(h.alice, "a1", "b1", 1_000)
    entries = r.json()["transaction"]["entries"]
    assert [e["account_id"] for e in entries] == [h.accounts["a1"]]

    tx_id = r.json()["transaction"]["id"]
    seen_by_bob = await h.client.get(f"/v1/transactions/{tx_id}", headers=h.bob.headers)
    assert [e["account_id"] for e in seen_by_bob.json()["transaction"]["entries"]] == [h.accounts["b1"]]


async def test_unauthenticated_and_forged_tokens_are_rejected(h):
    r = await h.client.post("/v1/deposits", json={"account_id": h.accounts["a1"], "amount": 1})
    assert r.status_code == 401
    forged = {"Authorization": "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4Iiwicm9sZSI6IkFETUlOIn0."}
    r = await h.client.get("/v1/ledger/entries", headers=forged)
    assert r.status_code == 401


async def test_admin_can_freeze_and_frozen_accounts_refuse_money_movement(h):
    await h.deposit(h.alice, "a1", 10_000)
    r = await h.client.post(
        f"/v1/accounts/{h.accounts['a1']}/status", json={"status": "FROZEN", "reason": "suspicious"},
        headers=h.admin.headers,
    )
    assert r.status_code == 200 and r.json()["account"]["status"] == "FROZEN"

    for call in (h.withdraw(h.alice, "a1", 1), h.transfer(h.alice, "a1", "b1", 1), h.transfer(h.bob, "b1", "a1", 1)):
        resp = await call
        assert resp.status_code in (422,), resp.text
        assert resp.json()["error"]["code"] in ("ACCOUNT_FROZEN", "INSUFFICIENT_FUNDS")
    assert await h.balance("a1") == 10_000
    flags = (await h.client.get("/v1/anomalies", headers=h.auditor.headers)).json()["anomalies"]
    assert any(f["target_id"] == h.accounts["a1"] for f in flags)
