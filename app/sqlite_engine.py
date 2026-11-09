"""Embedded SQLite fallback engine for LedgerLine.
Automatically activates when a local or remote PostgreSQL instance is not reachable.
Enforces double-entry balances, running totals, SHA-256 hash chains, and idempotency locks.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("ledgerline.sqlite")
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ledgerline.db")


def _convert_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, str) and (k == "id" or k.endswith("_id")) and v:
            try:
                d[k] = uuid.UUID(v)
            except ValueError:
                pass
        elif k in ("response_body", "details", "results") and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                pass
    return d


class SqliteCursorWrapper:
    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self) -> dict | None:
        row = self._cursor.fetchone()
        return _convert_row(row)

    async def fetchall(self) -> list[dict]:
        rows = self._cursor.fetchall()
        return [_convert_row(r) for r in rows if r is not None]


class SqliteTransactionContext:
    def __init__(self, conn: "SqliteConnectionWrapper"):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.conn._raw_conn.rollback()
        else:
            self.conn._raw_conn.commit()


class SqliteConnectionWrapper:
    def __init__(self, raw_conn: sqlite3.Connection, engine: "SqliteEngine"):
        self._raw_conn = raw_conn
        self._engine = engine

    def transaction(self):
        return SqliteTransactionContext(self)

    async def execute(self, query: str, params: Any = None) -> SqliteCursorWrapper:
        return await asyncio.to_thread(self._sync_execute, query, params)

    def _sync_execute(self, query: str, params: Any = None) -> SqliteCursorWrapper:
        # Ignore PostgreSQL-specific session configuration & transaction isolation queries
        if re.match(r"^\s*SET\s+", query, flags=re.IGNORECASE):
            cur = self._raw_conn.cursor()
            cur.execute("SELECT 1")
            return SqliteCursorWrapper(cur)

        # Handle PostgreSQL-specific pg_try_advisory_xact_lock
        if "pg_try_advisory_xact_lock" in query:
            lock_key = str(params[0]) if params else "default"
            if lock_key in self._engine._active_advisory_locks:
                cur = self._raw_conn.cursor()
                cur.execute("SELECT 0 as got, 0 as pg_try_advisory_xact_lock")
                return SqliteCursorWrapper(cur)
            else:
                self._engine._active_advisory_locks.add(lock_key)
                cur = self._raw_conn.cursor()
                cur.execute("SELECT 1 as got, 1 as pg_try_advisory_xact_lock")
                return SqliteCursorWrapper(cur)

        # Intercept INSERT INTO transactions to populate id and created_at if omitted
        if "INSERT INTO transactions" in query and "VALUES" in query:
            cols_match = re.search(r"INSERT\s+INTO\s+transactions\s*\((.*?)\)\s*VALUES", query, re.IGNORECASE | re.DOTALL)
            if cols_match and not re.search(r"\bid\b", cols_match.group(1)):
                tx_id = str(uuid.uuid4())
                now_str = datetime.now(timezone.utc).isoformat()
                clean_params = [tx_id]
                for p in (params or []):
                    clean_params.append(str(p) if isinstance(p, uuid.UUID) else p)
                clean_params.append(now_str)
                insert_sql = """
                INSERT INTO transactions (id, type, amount, currency, initiated_by, idempotency_key, description, external_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id, type, amount, currency, initiated_by, idempotency_key, description, external_ref, created_at
                """
                cur = self._raw_conn.cursor()
                cur.execute(insert_sql, clean_params)
                return SqliteCursorWrapper(cur)

        # Intercept INSERT INTO ledger_entries to compute hash chain, seq, and balance
        if "INSERT INTO ledger_entries" in query and "VALUES" in query:
            cols_match = re.search(r"INSERT\s+INTO\s+ledger_entries\s*\((.*?)\)\s*VALUES", query, re.IGNORECASE | re.DOTALL)
            if cols_match and not re.search(r"\baccount_seq\b", cols_match.group(1)):
                tx_id = str(params[0])
                acct_id = str(params[1])
                side = str(params[2])
                amount = int(params[3])
                new_balance = int(params[4])
                currency = str(params[5])

                cur_read = self._raw_conn.cursor()
                cur_read.execute("SELECT account_seq, entry_hash FROM ledger_entries WHERE account_id = ? ORDER BY account_seq DESC LIMIT 1", [acct_id])
                prev_row = cur_read.fetchone()
                seq = (prev_row[0] + 1) if prev_row else 1
                prev_hash = prev_row[1] if prev_row else ("0" * 64)

                entry_hash = hashlib.sha256(f"{prev_hash}:{seq}:{side}:{amount}:{new_balance}".encode()).hexdigest()
                entry_id = str(uuid.uuid4())
                now_str = datetime.now(timezone.utc).isoformat()

                insert_sql = """
                INSERT INTO ledger_entries (id, transaction_id, account_id, account_seq, entry_type, amount, balance_after, currency, prev_hash, entry_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id, account_id, account_seq, entry_type, amount, balance_after, currency, entry_hash, created_at
                """
                cur = self._raw_conn.cursor()
                cur.execute(insert_sql, [entry_id, tx_id, acct_id, seq, side, amount, new_balance, currency, prev_hash, entry_hash, now_str])
                return SqliteCursorWrapper(cur)

        clean_query = query

        # Strip PostgreSQL FOR UPDATE
        clean_query = re.sub(r"\bFOR\s+UPDATE\b", "", clean_query, flags=re.IGNORECASE)

        # Strip PostgreSQL typecasts (e.g., ::bigint, ::text, ::uuid, ::jsonb)
        clean_query = re.sub(r"::[a-zA-Z_]+", "", clean_query)

        # Transform PostgreSQL ANY(%s) -> IN (?, ?, ...)
        if params is not None:
            new_params = []
            if isinstance(params, (list, tuple)):
                p_idx = 0
                while "%s" in clean_query and p_idx < len(params):
                    val = params[p_idx]
                    p_idx += 1

                    # Extract Jsonb obj if present
                    if hasattr(val, "obj"):
                        val = val.obj

                    match_any = re.search(r"=\s*ANY\s*\(\s*%s\s*\)", clean_query, re.IGNORECASE)
                    if match_any and isinstance(val, (list, tuple, set)):
                        placeholders = ",".join(["?"] * len(val))
                        clean_query = clean_query[:match_any.start()] + f"IN ({placeholders})" + clean_query[match_any.end():]
                        new_params.extend([str(v) for v in val])
                    else:
                        clean_query = clean_query.replace("%s", "?", 1)
                        if isinstance(val, uuid.UUID):
                            new_params.append(str(val))
                        elif isinstance(val, dict):
                            new_params.append(json.dumps(val))
                        elif isinstance(val, bool):
                            new_params.append(1 if val else 0)
                        else:
                            new_params.append(val)
                params = new_params
            clean_query = clean_query.replace("%s", "?")

        cur = self._raw_conn.cursor()
        cur.execute(clean_query, params or [])
        return SqliteCursorWrapper(cur)


class SqliteConnectionPoolWrapper:
    def __init__(self, engine: "SqliteEngine"):
        self._engine = engine

    def connection(self):
        class ConnContext:
            def __init__(self, engine: "SqliteEngine"):
                self.engine = engine
                self.conn = None

            async def __aenter__(self):
                raw = sqlite3.connect(DB_FILE, check_same_thread=False)
                raw.row_factory = sqlite3.Row
                raw.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
                raw.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
                raw.execute("PRAGMA journal_mode = WAL")
                raw.execute("PRAGMA foreign_keys = ON")
                self.conn = SqliteConnectionWrapper(raw, self.engine)
                return self.conn

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                if self.conn:
                    self.conn._raw_conn.close()

        return ConnContext(self._engine)


class SqliteEngine:
    def __init__(self):
        self._active_advisory_locks: set[str] = set()
        self.pool = SqliteConnectionPoolWrapper(self)
        self.settlement_account_id: uuid.UUID | None = None

    def init_schema_and_seed(self):
        raw = sqlite3.connect(DB_FILE)
        raw.execute("PRAGMA journal_mode = WAL")
        raw.row_factory = sqlite3.Row
        raw.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        raw.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))
        cur = raw.cursor()

        # Create Tables precisely matching PostgreSQL schema
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()),
            email TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('ADMIN', 'ACCOUNT_HOLDER', 'AUDITOR')),
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (now())
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()),
            owner_id TEXT REFERENCES users(id),
            name TEXT NOT NULL,
            account_type TEXT NOT NULL CHECK(account_type IN ('USER', 'SYSTEM')),
            system_code TEXT UNIQUE,
            currency TEXT NOT NULL DEFAULT 'INR',
            balance INTEGER NOT NULL DEFAULT 0 CHECK(account_type = 'SYSTEM' OR balance >= 0),
            status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'FROZEN')),
            created_at TEXT NOT NULL DEFAULT (now())
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()),
            type TEXT NOT NULL CHECK(type IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER')),
            amount INTEGER NOT NULL CHECK(amount > 0),
            currency TEXT NOT NULL,
            initiated_by TEXT NOT NULL REFERENCES users(id),
            idempotency_key TEXT NOT NULL,
            description TEXT,
            external_ref TEXT,
            created_at TEXT NOT NULL DEFAULT (now()),
            UNIQUE(initiated_by, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS ledger_entries (
            id TEXT PRIMARY KEY DEFAULT (gen_random_uuid()),
            transaction_id TEXT NOT NULL REFERENCES transactions(id),
            account_id TEXT NOT NULL REFERENCES accounts(id),
            account_seq INTEGER NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN ('DEBIT', 'CREDIT')),
            amount INTEGER NOT NULL CHECK(amount > 0),
            balance_after INTEGER NOT NULL,
            currency TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (now()),
            UNIQUE(account_id, account_seq)
        );

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            user_id TEXT NOT NULL REFERENCES users(id),
            idempotency_key TEXT NOT NULL,
            request_method TEXT NOT NULL,
            request_path TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('IN_FLIGHT', 'COMPLETED')),
            response_code INTEGER,
            response_body TEXT,
            transaction_id TEXT,
            created_at TEXT NOT NULL DEFAULT (now()),
            completed_at TEXT,
            PRIMARY KEY(user_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS refusal_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attempted_by TEXT,
            actor_role TEXT,
            action TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            http_status INTEGER NOT NULL,
            request_path TEXT,
            idempotency_key TEXT,
            source_account_id TEXT,
            destination_account_id TEXT,
            requested_amount INTEGER,
            account_balance_snapshot INTEGER,
            details TEXT DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (now())
        );

        CREATE TABLE IF NOT EXISTS anomaly_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            reason TEXT NOT NULL,
            flagged_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (now())
        );

        CREATE TABLE IF NOT EXISTS invariant_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_by TEXT NOT NULL,
            passed INTEGER NOT NULL,
            results TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (now())
        );
        """)

        # Check if users already seeded
        cur.execute("SELECT count(*) as cnt FROM users")
        if cur.fetchone()["cnt"] == 0:
            from .security import hash_password
            now_iso = datetime.now(timezone.utc).isoformat()
            p_hash = hash_password("ledgerline-demo", iterations=10_000)

            # Insert Users
            user_admin = "11111111-1111-1111-1111-111111111111"
            user_auditor = "22222222-2222-2222-2222-222222222222"
            user_alice = "33333333-3333-3333-3333-333333333333"
            user_bob = "44444444-4444-4444-4444-444444444444"

            users_seed = [
                (user_admin, "admin@ledgerline.dev", "Asha Admin", "ADMIN", p_hash, 1, now_iso),
                (user_auditor, "auditor@ledgerline.dev", "Ravi Auditor", "AUDITOR", p_hash, 1, now_iso),
                (user_alice, "alice@ledgerline.dev", "Alice Rao", "ACCOUNT_HOLDER", p_hash, 1, now_iso),
                (user_bob, "bob@ledgerline.dev", "Bob Mehta", "ACCOUNT_HOLDER", p_hash, 1, now_iso),
            ]
            cur.executemany("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?)", users_seed)

            # Insert Settlement Account (System)
            settlement_id = "00000000-0000-0000-0000-000000000001"
            alice_main = "33333333-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
            alice_savings = "33333333-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            bob_main = "44444444-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

            cur.execute("""
            INSERT INTO accounts (id, owner_id, name, account_type, system_code, currency, balance, status, created_at)
            VALUES (?, NULL, 'Settlement / Clearing', 'SYSTEM', 'SETTLEMENT', 'INR', -150000, 'ACTIVE', ?)
            """, (settlement_id, now_iso))

            cur.execute("INSERT INTO accounts VALUES (?, ?, 'Alice — Main', 'USER', NULL, 'INR', 100000, 'ACTIVE', ?)", (alice_main, user_alice, now_iso))
            cur.execute("INSERT INTO accounts VALUES (?, ?, 'Alice — Savings', 'USER', NULL, 'INR', 20000, 'ACTIVE', ?)", (alice_savings, user_alice, now_iso))
            cur.execute("INSERT INTO accounts VALUES (?, ?, 'Bob — Main', 'USER', NULL, 'INR', 30000, 'ACTIVE', ?)", (bob_main, user_bob, now_iso))

            # Helper for hash chain calculation
            genesis_hash = "0" * 64

            def calc_hash(prev: str, seq: int, direction: str, amt: int, bal: str) -> str:
                return hashlib.sha256(f"{prev}:{seq}:{direction}:{amt}:{bal}".encode()).hexdigest()

            # Seed Initial Deposit Transaction 1: Alice Main (₹1,000.00 / 100,000 paise)
            tx1_id = "aaaaaaaa-1111-1111-1111-111111111111"
            cur.execute("""
            INSERT INTO transactions (id, type, amount, currency, initiated_by, idempotency_key, description, external_ref, created_at)
            VALUES (?, 'DEPOSIT', 100000, 'INR', ?, 'seed-deposit-alice-main', 'Opening deposit', 'EXT-DEP-1001', ?)
            """, (tx1_id, user_alice, now_iso))

            # Leg 1: Settlement DEBIT
            s_hash1 = calc_hash(genesis_hash, 1, "DEBIT", 100000, "-100000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 1, 'DEBIT', 100000, -100000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx1_id, settlement_id, genesis_hash, s_hash1, now_iso))

            # Leg 2: Alice Main CREDIT
            a_hash1 = calc_hash(genesis_hash, 1, "CREDIT", 100000, "100000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 1, 'CREDIT', 100000, 100000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx1_id, alice_main, genesis_hash, a_hash1, now_iso))

            # Seed Initial Deposit Transaction 2: Alice Savings (₹200.00 / 20,000 paise)
            tx2_id = "aaaaaaaa-2222-2222-2222-222222222222"
            cur.execute("""
            INSERT INTO transactions (id, type, amount, currency, initiated_by, idempotency_key, description, external_ref, created_at)
            VALUES (?, 'DEPOSIT', 20000, 'INR', ?, 'seed-deposit-alice-savings', 'Opening deposit', 'EXT-DEP-1002', ?)
            """, (tx2_id, user_alice, now_iso))

            s_hash2 = calc_hash(s_hash1, 2, "DEBIT", 20000, "-120000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 2, 'DEBIT', 20000, -120000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx2_id, settlement_id, s_hash1, s_hash2, now_iso))

            as_hash1 = calc_hash(genesis_hash, 1, "CREDIT", 20000, "20000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 1, 'CREDIT', 20000, 20000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx2_id, alice_savings, genesis_hash, as_hash1, now_iso))

            # Seed Initial Deposit Transaction 3: Bob Main (₹300.00 / 30,000 paise)
            tx3_id = "aaaaaaaa-3333-3333-3333-333333333333"
            cur.execute("""
            INSERT INTO transactions (id, type, amount, currency, initiated_by, idempotency_key, description, external_ref, created_at)
            VALUES (?, 'DEPOSIT', 30000, 'INR', ?, 'seed-deposit-bob-main', 'Opening deposit', 'EXT-DEP-1003', ?)
            """, (tx3_id, user_bob, now_iso))

            s_hash3 = calc_hash(s_hash2, 3, "DEBIT", 30000, "-150000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 3, 'DEBIT', 30000, -150000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx3_id, settlement_id, s_hash2, s_hash3, now_iso))

            b_hash1 = calc_hash(genesis_hash, 1, "CREDIT", 30000, "30000")
            cur.execute("""
            INSERT INTO ledger_entries VALUES (?, ?, ?, 1, 'CREDIT', 30000, 30000, 'INR', ?, ?, ?)
            """, (str(uuid.uuid4()), tx3_id, bob_main, genesis_hash, b_hash1, now_iso))

            raw.commit()
            log.info("SQLite engine initialized with seeded demo accounts, initial transactions, and ledger hash chains.")

        cur.execute("SELECT id FROM accounts WHERE system_code = 'SETTLEMENT'")
        row = cur.fetchone()
        if row:
            self.settlement_account_id = uuid.UUID(row["id"])
        raw.close()
