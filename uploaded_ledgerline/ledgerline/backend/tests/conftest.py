"""Test harness.

Each test gets a freshly rebuilt schema in a real PostgreSQL database (no mocks —
the point is to exercise real row locks, constraints and triggers). The API
connects as the least-privilege `ledgerline_app` role, exactly like production.

Environment:
  TEST_SUPERUSER_URL  default postgresql://postgres@localhost:5432/ledgerline_test
  TEST_APP_URL        default postgresql://ledgerline_app:ledgerline_app@localhost:5432/ledgerline_test
"""

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import Settings
from app.main import create_app
from app.seed import seed

SUPERUSER_URL = os.getenv("TEST_SUPERUSER_URL", "postgresql://postgres@localhost:5432/ledgerline_test")
APP_URL = os.getenv("TEST_APP_URL", "postgresql://ledgerline_app:ledgerline_app@localhost:5432/ledgerline_test")
DB_DIR = Path(__file__).resolve().parent.parent / "db"
PASSWORD = "test-password-123"


def reset_database() -> None:
    with psycopg.connect(SUPERUSER_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.execute((DB_DIR / "schema.sql").read_text())
        conn.execute((DB_DIR / "roles.sql").read_text())


def superuser_conn() -> psycopg.Connection:
    return psycopg.connect(SUPERUSER_URL, row_factory=dict_row)


def app_role_conn() -> psycopg.Connection:
    return psycopg.connect(APP_URL, row_factory=dict_row)


@dataclass
class Actor:
    email: str
    token: str
    user_id: str

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class Harness:
    client: httpx.AsyncClient
    app: object
    actors: dict[str, Actor] = field(default_factory=dict)
    accounts: dict[str, str] = field(default_factory=dict)

    @property
    def alice(self) -> Actor:
        return self.actors["alice"]

    @property
    def bob(self) -> Actor:
        return self.actors["bob"]

    @property
    def admin(self) -> Actor:
        return self.actors["admin"]

    @property
    def auditor(self) -> Actor:
        return self.actors["auditor"]

    async def post(self, actor: Actor, path: str, body: dict, key: str | None = "auto") -> httpx.Response:
        headers = dict(actor.headers)
        if key == "auto":
            key = f"k-{uuid.uuid4()}"
        if key is not None:
            headers["Idempotency-Key"] = key
        return await self.client.post(path, json=body, headers=headers)

    async def deposit(self, actor: Actor, account: str, amount: int, key: str | None = "auto"):
        return await self.post(actor, "/v1/deposits", {"account_id": self.accounts[account], "amount": amount}, key)

    async def withdraw(self, actor: Actor, account: str, amount: int, key: str | None = "auto"):
        return await self.post(actor, "/v1/withdrawals", {"account_id": self.accounts[account], "amount": amount}, key)

    async def transfer(self, actor: Actor, src: str, dst: str, amount: int, key: str | None = "auto"):
        return await self.post(
            actor,
            "/v1/transfers",
            {"source_account_id": self.accounts[src], "destination_account_id": self.accounts[dst], "amount": amount},
            key,
        )

    async def balance(self, account: str) -> int:
        r = await self.client.get(f"/v1/accounts/{self.accounts[account]}", headers=self.admin.headers)
        assert r.status_code == 200, r.text
        return r.json()["account"]["balance"]

    async def verify(self) -> dict:
        r = await self.client.post("/v1/invariants/verify", headers=self.auditor.headers)
        assert r.status_code == 200, r.text
        return r.json()

    async def assert_invariants(self) -> dict:
        report = await self.verify()
        failed = [c for c in report["checks"] if not c["passed"]]
        assert report["passed"], f"invariants failed: {failed}"
        return report


def count(sql: str, params=None) -> int:
    with superuser_conn() as conn:
        return conn.execute(sql, params).fetchone()["n"]


@pytest.fixture
async def h() -> Harness:
    reset_database()
    settings = Settings(
        database_url=APP_URL,
        jwt_secret="test-secret-test-secret-test-secret",
        pool_min_size=2,
        pool_max_size=40,
        password_hash_iterations=1_000,  # fast hashing in tests only
    )
    app = create_app(settings)
    await app.state.db.open()

    # Seeded users plus a little demo history; tests create their own empty accounts below.
    data = await seed(app.state.db, password=PASSWORD)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
        harness = Harness(client=client, app=app)
        for name in ("alice", "bob", "admin", "auditor"):
            email = f"{name}@ledgerline.dev"
            r = await client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert r.status_code == 200, r.text
            harness.actors[name] = Actor(email=email, token=r.json()["access_token"], user_id=r.json()["user"]["id"])

        # Fresh, empty accounts for each test (seeded accounts keep their demo history).
        for label, owner in (("a1", "alice"), ("a2", "alice"), ("b1", "bob")):
            r = await client.post(
                "/v1/accounts",
                json={"owner_id": harness.actors[owner].user_id, "name": f"test-{label}"},
                headers=harness.admin.headers,
            )
            assert r.status_code == 201, r.text
            harness.accounts[label] = r.json()["account"]["id"]
        harness.accounts["alice_main"] = str(data["accounts"]["Alice — Main"]["id"])
        harness.accounts["bob_main"] = str(data["accounts"]["Bob — Main"]["id"])
        yield harness
    await app.state.db.close()
