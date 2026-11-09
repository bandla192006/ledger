"""Seed demo users, accounts and a little history. Safe to run repeatedly.

    python -m app.seed
"""

import asyncio
import os
import sys

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from .config import get_settings
from .db import Database, fetchone
from .idempotency import execute_idempotent
from .money import deposit, transfer
from .schemas import DepositRequest, TransferRequest
from .security import Principal, hash_password

DEMO_PASSWORD = os.getenv("SEED_PASSWORD", "ledgerline-demo")

USERS = [
    ("admin@ledgerline.dev", "Asha Admin", "ADMIN"),
    ("auditor@ledgerline.dev", "Ravi Auditor", "AUDITOR"),
    ("alice@ledgerline.dev", "Alice Rao", "ACCOUNT_HOLDER"),
    ("bob@ledgerline.dev", "Bob Mehta", "ACCOUNT_HOLDER"),
]
ACCOUNTS = [
    ("alice@ledgerline.dev", "Alice — Main"),
    ("alice@ledgerline.dev", "Alice — Savings"),
    ("bob@ledgerline.dev", "Bob — Main"),
]


async def seed(db: Database, password: str = DEMO_PASSWORD) -> dict:
    users: dict[str, dict] = {}
    accounts: dict[str, dict] = {}
    async with db.pool.connection() as conn:
        async with conn.transaction():
            for email, name, role in USERS:
                row = await fetchone(conn, "SELECT * FROM users WHERE email = %s", [email])
                if row is None:
                    row = await fetchone(
                        conn,
                        "INSERT INTO users (email, full_name, role, password_hash) VALUES (%s,%s,%s,%s) RETURNING *",
                        [email, name, role, hash_password(password, db.settings.password_hash_iterations)],
                    )
                users[email] = row
            for email, acct_name in ACCOUNTS:
                owner = users[email]["id"]
                row = await fetchone(conn, "SELECT * FROM accounts WHERE owner_id = %s AND name = %s",
                                     [owner, acct_name])
                if row is None:
                    row = await fetchone(
                        conn,
                        "INSERT INTO accounts (owner_id, name, account_type) VALUES (%s, %s, 'USER') RETURNING *",
                        [owner, acct_name],
                    )
                accounts[acct_name] = row

    def principal(email: str) -> Principal:
        u = users[email]
        return Principal(id=u["id"], email=u["email"], full_name=u["full_name"], role=u["role"])

    alice, bob = principal("alice@ledgerline.dev"), principal("bob@ledgerline.dev")
    a_main, a_sav, b_main = (accounts[n]["id"] for n in ("Alice — Main", "Alice — Savings", "Bob — Main"))

    async def run(p: Principal, key: str, path: str, req, handler):
        await execute_idempotent(db, principal=p, key=key, method="POST", path=path,
                                 payload=req.model_dump(mode="json"), action="SEED", handler=handler)

    d1 = DepositRequest(account_id=a_main, amount=2_500_000, description="Salary", external_ref="NEFT-SEED-001")
    await run(alice, "seed-alice-deposit-1", "/v1/deposits", d1,
              lambda c: deposit(c, alice, d1, "seed-alice-deposit-1", db.settlement_account_id))
    d2 = DepositRequest(account_id=b_main, amount=800_000, description="Opening deposit", external_ref="UPI-SEED-002")
    await run(bob, "seed-bob-deposit-1", "/v1/deposits", d2,
              lambda c: deposit(c, bob, d2, "seed-bob-deposit-1", db.settlement_account_id))
    t1 = TransferRequest(source_account_id=a_main, destination_account_id=a_sav, amount=1_000_000,
                         description="Move to savings")
    await run(alice, "seed-alice-transfer-1", "/v1/transfers", t1,
              lambda c: transfer(c, alice, t1, "seed-alice-transfer-1"))
    t2 = TransferRequest(source_account_id=a_main, destination_account_id=b_main, amount=125_050,
                         description="Dinner split")
    await run(alice, "seed-alice-transfer-2", "/v1/transfers", t2,
              lambda c: transfer(c, alice, t2, "seed-alice-transfer-2"))
    return {"users": users, "accounts": accounts}


async def main() -> None:
    db = Database(get_settings())
    await db.open()
    try:
        await seed(db)
    finally:
        await db.close()
    print("Seeded. Log in with any of:")
    for email, _, role in USERS:
        print(f"  {role:<15} {email:<26} password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
