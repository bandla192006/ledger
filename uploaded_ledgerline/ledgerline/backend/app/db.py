from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Settings


class Database:
    """Thin wrapper around a psycopg async pool.

    Connections run in autocommit mode; every unit of work opens an explicit
    `async with conn.transaction():` block, and nested blocks become SAVEPOINTs.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool: AsyncConnectionPool | None = None
        self.settlement_account_id: UUID | None = None

    async def _configure(self, conn: AsyncConnection) -> None:
        await conn.execute(f"SET lock_timeout = {int(self.settings.lock_timeout_ms)}")
        await conn.execute(f"SET statement_timeout = {int(self.settings.statement_timeout_ms)}")
        await conn.execute("SET application_name = 'ledgerline-api'")

    async def open(self) -> None:
        self.pool = AsyncConnectionPool(
            self.settings.database_url,
            min_size=self.settings.pool_min_size,
            max_size=self.settings.pool_max_size,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=self._configure,
            open=False,
        )
        await self.pool.open(wait=True)
        async with self.pool.connection() as conn:
            row = await fetchone(conn, "SELECT id FROM accounts WHERE system_code = 'SETTLEMENT'")
            if row is None:
                raise RuntimeError("Settlement account missing — did you apply db/schema.sql?")
            self.settlement_account_id = row["id"]

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None


async def fetchone(conn: AsyncConnection, query: str, params: Any = None) -> dict | None:
    cur = await conn.execute(query, params)
    return await cur.fetchone()


async def fetchall(conn: AsyncConnection, query: str, params: Any = None) -> list[dict]:
    cur = await conn.execute(query, params)
    return await cur.fetchall()
