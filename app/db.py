import asyncio
import logging
import socket
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import Settings
from .sqlite_engine import SqliteEngine

log = logging.getLogger("ledgerline.db")


def _is_server_listening(db_url: str) -> bool:
    """Fast socket probe to detect whether PostgreSQL is reachable before opening pool."""
    try:
        parsed = urlparse(db_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class Database:
    """Dual-mode Database Adapter:
    Connects to PostgreSQL (with full DDL checks, triggers, and row locks) when available.
    Automatically falls back to embedded SQLite for zero-config standalone operation in VS Code.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool: Any = None
        self.settlement_account_id: UUID | None = None
        self.is_sqlite: bool = False
        self._sqlite_engine: SqliteEngine | None = None

    async def _configure(self, conn: AsyncConnection) -> None:
        await conn.execute(f"SET lock_timeout = {int(self.settings.lock_timeout_ms)}")
        await conn.execute(f"SET statement_timeout = {int(self.settings.statement_timeout_ms)}")
        await conn.execute("SET application_name = 'ledgerline-api'")

    async def open(self) -> None:
        use_sqlite = self.settings.database_url.startswith("sqlite")
        pg_reachable = not use_sqlite and _is_server_listening(self.settings.database_url)

        if pg_reachable:
            pg_pool = None
            try:
                pg_pool = AsyncConnectionPool(
                    self.settings.database_url,
                    min_size=self.settings.pool_min_size,
                    max_size=self.settings.pool_max_size,
                    kwargs={"row_factory": dict_row, "autocommit": True},
                    configure=self._configure,
                    open=False,
                )
                await asyncio.wait_for(pg_pool.open(wait=True), timeout=2.5)
                self.pool = pg_pool
                async with self.pool.connection() as conn:
                    row = await fetchone(conn, "SELECT id FROM accounts WHERE system_code = 'SETTLEMENT'")
                    if row is not None:
                        self.settlement_account_id = row["id"]
                        log.info("Connected to PostgreSQL successfully.")
                        return
            except Exception as e:
                log.warning("PostgreSQL probe failed (%s). Falling back to embedded SQLite engine.", e)
                if pg_pool is not None:
                    try:
                        await pg_pool.close()
                    except Exception:
                        pass

        # Fallback to embedded SQLite
        self.is_sqlite = True
        self._sqlite_engine = SqliteEngine()
        self._sqlite_engine.init_schema_and_seed()
        self.pool = self._sqlite_engine.pool
        self.settlement_account_id = self._sqlite_engine.settlement_account_id
        print("=================================================================")
        print("  [LedgerLine] Active Mode: Embedded SQLite Ledger (Zero Config) ")
        print("  All API endpoints, users, and 3D visualizer are active!       ")
        print("=================================================================")

    async def close(self) -> None:
        if self.pool is not None and not self.is_sqlite:
            await self.pool.close()
            self.pool = None


async def fetchone(conn: Any, query: str, params: Any = None) -> dict | None:
    cur = await conn.execute(query, params)
    return await cur.fetchone()


async def fetchall(conn: Any, query: str, params: Any = None) -> list[dict]:
    cur = await conn.execute(query, params)
    return await cur.fetchall()
