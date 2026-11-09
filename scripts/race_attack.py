"""Concurrent load / attack script against a RUNNING LedgerLine server.

Unlike the pytest suite (in-process), this hits real HTTP across uvicorn worker
processes, so races happen between OS processes, not just coroutines.

    uvicorn app.main:app --workers 4 --port 8000
    python scripts/race_attack.py --base-url http://localhost:8000 --concurrency 200
"""

import argparse
import asyncio
import collections
import random
import sys
import time
import uuid

import httpx

PASSWORD = "ledgerline-demo"


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    r = await client.post("/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def new_account(client, admin, owner_id: str, name: str) -> str:
    r = await client.post("/v1/accounts", json={"owner_id": owner_id, "name": name}, headers=admin)
    r.raise_for_status()
    return r.json()["account"]["id"]


async def post(client, headers, path, body, key=None):
    return await client.post(path, json=body, headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())})


async def balance(client, admin, account_id) -> int:
    r = await client.get(f"/v1/accounts/{account_id}", headers=admin)
    return r.json()["account"]["balance"]


def report(title: str, responses, ok: bool, extra: str = "") -> bool:
    codes = collections.Counter(r.status_code for r in responses)
    print(f"[{'PASS' if ok else 'FAIL'}] {title}: {dict(codes)} {extra}")
    return ok


async def main(base_url: str, concurrency: int, password: str) -> int:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base_url, timeout=60, limits=limits) as client:
        admin = await login(client, "admin@ledgerline.dev", password)
        auditor = await login(client, "auditor@ledgerline.dev", password)
        alice = await login(client, "alice@ledgerline.dev", password)
        bob = await login(client, "bob@ledgerline.dev", password)
        users = (await client.get("/v1/users", headers=admin)).json()["users"]
        uid = {u["email"]: u["id"] for u in users}
        tag = uuid.uuid4().hex[:6]
        a = await new_account(client, admin, uid["alice@ledgerline.dev"], f"race-a-{tag}")
        b = await new_account(client, admin, uid["bob@ledgerline.dev"], f"race-b-{tag}")
        results = []
        start = time.perf_counter()

        # 1. Double spend: N withdrawals of the entire balance at once.
        await post(client, alice, "/v1/deposits", {"account_id": a, "amount": 100_000})
        rs = await asyncio.gather(*(post(client, alice, "/v1/withdrawals", {"account_id": a, "amount": 100_000})
                                    for _ in range(concurrency)))
        wins = sum(r.status_code == 201 for r in rs)
        results.append(report("double-spend withdrawals", rs, wins == 1 and await balance(client, admin, a) == 0,
                              f"successes={wins}"))

        # 2. Same idempotency key fired N times concurrently.
        await post(client, alice, "/v1/deposits", {"account_id": a, "amount": 1_000_000})
        key = f"race-{tag}"
        rs = await asyncio.gather(*(post(client, alice, "/v1/transfers",
                                         {"source_account_id": a, "destination_account_id": b, "amount": 1_000},
                                         key=key) for _ in range(concurrency)))
        ids = {r.json()["transaction"]["id"] for r in rs if r.status_code == 201}
        ok = len(ids) == 1 and await balance(client, admin, b) == 1_000
        results.append(report("duplicate idempotency key", rs, ok, f"distinct transactions={len(ids)}"))

        # 3. Opposing transfers A<->B (deadlock bait).
        await post(client, bob, "/v1/deposits", {"account_id": b, "amount": 1_000_000})
        total_before = await balance(client, admin, a) + await balance(client, admin, b)
        rng = random.Random(1)
        calls = [
            post(client, alice, "/v1/transfers", {"source_account_id": a, "destination_account_id": b,
                                                  "amount": rng.randint(1, 3_000)})
            if i % 2 == 0 else
            post(client, bob, "/v1/transfers", {"source_account_id": b, "destination_account_id": a,
                                                "amount": rng.randint(1, 3_000)})
            for i in range(concurrency)
        ]
        rs = await asyncio.gather(*calls)
        total_after = await balance(client, admin, a) + await balance(client, admin, b)
        ok = all(r.status_code in (201, 422) for r in rs) and total_before == total_after
        results.append(report("opposing transfers", rs, ok, f"total {total_before} -> {total_after}"))

        elapsed = time.perf_counter() - start

        # 4. Prove it.
        proof = (await client.post("/v1/invariants/verify", headers=auditor)).json()
        print()
        for c in proof["checks"]:
            print(f"  {'✔' if c['passed'] else '✘'} {c['name']}")
        results.append(report("invariant verification", [], proof["passed"]))
        print(f"\n{3 * concurrency + 3} money requests in {elapsed:.2f}s")
        return 0 if all(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--password", default=PASSWORD)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.base_url, args.concurrency, args.password)))
