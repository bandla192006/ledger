# LedgerLine

A double-entry ledger and money-movement engine. Every money rule is enforced twice: once in the API, and again inside PostgreSQL, so a bug in application code (or someone at a `psql` prompt) still can't create, destroy, or rewrite money.

- **Backend:** Python 3.12+, FastAPI, psycopg 3 (raw SQL, async pool)
- **Database:** PostgreSQL 14+ (constraints, triggers, row locks)
- **Frontend:** React 19, Vite, Tailwind CSS v4, TypeScript
- **Tests:** pytest against a real PostgreSQL (69 tests, no mocks), plus a multi-process HTTP attack script

---

## Quick start

### Docker

```bash
docker compose up --build
# Web UI   → http://localhost:5173
# API docs → http://localhost:8000/docs
```

### Local (no Docker)

```bash
# 1. Database
createdb ledgerline
psql -d ledgerline -f backend/db/schema.sql
psql -d ledgerline -f backend/db/roles.sql      # creates the least-privilege app role

# 2. API
cd backend
pip install -r requirements-dev.txt
python -m app.seed                               # demo users, accounts, a little history
uvicorn app.main:app --reload --port 8000

# 3. Web
cd ../frontend
npm install
npm run dev                                      # http://localhost:5173 (proxies /v1 to :8000)
```

### Demo users (password `ledgerline-demo`)

| Role | Email | Can |
|---|---|---|
| Account holder | `alice@ledgerline.dev` | Deposit, withdraw and transfer from her own accounts; view her statements |
| Account holder | `bob@ledgerline.dev` | Same, for Bob's account |
| Admin | `admin@ledgerline.dev` | Create users and accounts, freeze/unfreeze, view the global ledger and refusals, flag anomalies, run verification |
| Auditor | `auditor@ledgerline.dev` | Read-only access to everything (statements, ledger, refusals, idempotency records); run verification |

The login screen has one-click buttons for each.

---

## Running the tests

```bash
createdb ledgerline_test
cd backend
pytest -q                     # 69 tests, ~15 s; rebuilds the schema before every test
```

Set `TEST_SUPERUSER_URL` / `TEST_APP_URL` if your Postgres isn't at `localhost:5432` with user `postgres`.

**Multi-process attack** against a running server, so races happen between OS processes rather than coroutines:

```bash
uvicorn app.main:app --workers 4 --port 8000
python scripts/race_attack.py --concurrency 200
```

Output from the last run:

```
[PASS] double-spend withdrawals: {201: 1, 422: 199} successes=1
[PASS] duplicate idempotency key: {201: 195, 409: 5} distinct transactions=1
[PASS] opposing transfers: {201: 200} total 2000000 -> 2000000
  ✔ double_entry_balanced          ✔ cached_balance_matches_ledger
  ✔ global_debits_equal_credits    ✔ running_balance_continuity
  ✔ zero_sum_balances              ✔ hash_chain_intact
  ✔ external_flow_reconciles       ✔ idempotency_consistent
  ✔ internal_transfers_net_zero    ✔ no_negative_user_balances
603 money requests in 7.75s
```

---

## How each requirement is guaranteed

| Requirement | Application layer | Database layer | Proven by |
|---|---|---|---|
| **No money created or lost** | Every posting writes one DEBIT and one equal CREDIT | Deferred constraint trigger `check_transaction_balanced` rejects at COMMIT any transaction where `sum(DEBIT) ≠ sum(CREDIT) ≠ amount` or with fewer than 2 legs | `test_unbalanced_transaction_cannot_commit`, invariants `double_entry_balanced`, `zero_sum_balances` |
| **External delta = deposits − withdrawals** | Deposits DEBIT the Settlement/Clearing account; withdrawals CREDIT it | Settlement is the only account allowed below zero; a CHECK keeps it ≤ 0 | invariant `external_flow_reconciles`, `test_external_flow_equals_deposits_minus_withdrawals` |
| **No overdrafts** | Funds checked against the *locked* row | `CHECK (account_type = 'SYSTEM' OR balance >= 0)` | `test_racing_withdrawals_never_double_spend`, `test_database_alone_blocks_overdraft_when_app_check_is_disabled` |
| **Balances can't drift from the ledger** | Balance updated in the same transaction as its entry | Deferred triggers on `accounts` and `ledger_entries`: stored balance must equal the latest `balance_after`; BEFORE INSERT trigger checks the running total | `test_balance_cannot_change_without_a_ledger_entry`, `test_forged_running_balance_is_rejected` |
| **Append-only, tamper-evident** | No UPDATE/DELETE code paths exist | 1) The app role has no UPDATE/DELETE/TRUNCATE privilege. 2) Triggers raise on UPDATE/DELETE/TRUNCATE, even for the superuser. 3) Per-account SHA-256 hash chain, computed by the DB and re-verified independently in Python | `test_app_role_has_no_privilege_to_mutate_history`, `test_even_superuser_is_stopped_by_triggers`, `test_hash_chain_detects_edits_made_with_triggers_disabled` |
| **Idempotency** | Key, ledger rows and cached response commit in one transaction | `UNIQUE (initiated_by, idempotency_key)` on `transactions`; completed keys are frozen by a trigger | `test_racing_duplicates_post_exactly_once`, `test_same_key_different_payload_is_rejected` |
| **No deadlocks** | One `SELECT … WHERE id = ANY(…) ORDER BY id FOR UPDATE` per request | Postgres row locks, always taken in ascending id order | `test_opposing_transfers_do_not_deadlock` (120 concurrent A→B / B→A) |
| **Every refusal audited** | `Refused` exceptions are logged in the same transaction; RBAC, auth and validation failures are logged separately | `refusal_audit_logs` is append-only | assertions on refusal counts throughout the suite |

### The posting path

```
POST /v1/transfers  (Idempotency-Key: k)
│
├─ RBAC: only ACCOUNT_HOLDER may move money ─────────────── 403 FORBIDDEN_ROLE (audited)
├─ Pydantic: amount must be a strict positive int ≤ 10¹² ── 422 VALIDATION_ERROR (audited)
│
└─ BEGIN
   ├─ pg_try_advisory_xact_lock(user, k)  busy? ─────────── 409 CONCURRENT_CONFLICT
   ├─ SELECT idempotency_keys
   │    COMPLETED, same fingerprint ─────────────────────── return cached response (Idempotent-Replayed: true)
   │    different fingerprint ───────────────────────────── 422 IDEMPOTENCY_KEY_REUSED
   ├─ INSERT key (IN_FLIGHT)
   ├─ SELECT … FROM accounts WHERE id = ANY(src, dst) ORDER BY id FOR UPDATE
   ├─ ownership / frozen / currency / funds  ─────────────── refusal → audited + cached
   ├─ SAVEPOINT
   │    INSERT transactions; INSERT 2× ledger_entries (trigger: seq, hash, running-balance check)
   │    UPDATE accounts SET balance  (CHECK balance >= 0)
   ├─ UPDATE key → COMPLETED (response_code, response_body, transaction_id)
   └─ COMMIT  (deferred triggers: balanced? balances match ledger?)
```

Because the key row, the postings and the cached response commit together, there is no window where money moved but the response wasn't stored, and a crash can't leave a key stuck `IN_FLIGHT`; the whole transaction rolls back.

---

## Design decisions

These are the places where I chose one option over another, or departed from the brief.

1. **Idempotency keys are scoped per user** (`PRIMARY KEY (user_id, idempotency_key)`, not just `idempotency_key`). With a global key, Bob reusing a key Alice had used would either replay *Alice's* response (a data leak) or fail unpredictably. All the brief's columns are still there.
2. **`IN_FLIGHT` detection uses a transaction-scoped advisory lock.** The key row is written inside the posting transaction, so other sessions can't see the `IN_FLIGHT` row before COMMIT. The advisory lock is what makes the brief's "in flight → 409" observable without a separate commit, which would risk stuck keys.
3. **Refusals are cached too.** Retrying a request that was refused for insufficient funds returns the same 422 even if money arrived since. Retrying with a key means "give me the answer to *that* request"; a new attempt needs a new key. The frontend handles this by generating a new key whenever the form changes.
4. **READ COMMITTED + explicit `FOR UPDATE`, not SERIALIZABLE.** Locking the rows gives deterministic results without serialization-failure retry loops. After a lock wait, Postgres re-reads the row, so the funds check always sees the latest committed balance. Deadlocks and serialization failures are still retried defensively (up to 4 attempts).
5. **The DB assigns `account_seq`, `prev_hash`, `entry_hash` and `created_at`** in a BEFORE INSERT trigger, so callers can't forge them. The Python verifier re-implements the hash (`app/hashing.py`) rather than calling the SQL function, so a tampered function can't vouch for tampered rows.
6. **Admins and auditors can't move money** (separation of duties). Admins manage accounts and people; auditors only read and verify.
7. **Holders never see a counterparty's balance.** Transfer responses and `/v1/transactions/{id}` show only legs on accounts the caller owns; statements show the settlement account as "External".
8. **Amounts are integer paise** (`BIGINT`), validated with `StrictInt` (rejects `1.5`, `"100"`, `true`). They're capped at 10¹² per transaction, which keeps them within JavaScript's safe-integer range.
9. **The settlement account is a hot row.** Every deposit and withdrawal locks it, which serializes external flows. That's acceptable at this scale and keeps the invariants simple. At high volume I'd shard it into N clearing accounts and pick one by hash, since the invariants already sum over all SYSTEM accounts.

---

## API

All money endpoints require `Authorization: Bearer <jwt>` and an `Idempotency-Key` header. Errors are `{"error": {"code", "message", "details"}}`. Interactive docs are at `/docs`.

| Method | Path | Roles |
|---|---|---|
| POST | `/v1/auth/login` | public |
| GET | `/v1/auth/me` | any |
| POST | `/v1/deposits` · `/v1/withdrawals` · `/v1/transfers` | ACCOUNT_HOLDER |
| GET | `/v1/accounts` | any (holders: own only) |
| GET | `/v1/accounts/directory` | any (payee list, no balances) |
| POST | `/v1/accounts` | ADMIN |
| GET | `/v1/accounts/{id}` · `/v1/accounts/{id}/statement` | owner, ADMIN, AUDITOR |
| POST | `/v1/accounts/{id}/status` | ADMIN |
| GET | `/v1/transactions` · `/v1/transactions/{id}` | any (holders: own legs only) |
| GET | `/v1/ledger/entries` | ADMIN, AUDITOR |
| GET | `/v1/audit/refusals` · `/v1/audit/idempotency-keys` | ADMIN, AUDITOR |
| POST | `/v1/invariants/verify` · GET `/v1/invariants/runs` | ADMIN, AUDITOR |
| POST | `/v1/anomalies` | ADMIN |
| GET | `/v1/anomalies` · `/v1/users` | ADMIN, AUDITOR |
| POST | `/v1/users` | ADMIN |

---

## Layout

```
backend/
  db/schema.sql         tables, CHECKs, append-only + invariant triggers, hash chain
  db/roles.sql          least-privilege ledgerline_app role
  app/idempotency.py    the idempotency engine
  app/money.py          posting engine: ordered locks, validation, postings
  app/invariants.py     10 invariant checks over one REPEATABLE READ snapshot
  app/hashing.py        independent hash-chain implementation
  app/routers/          HTTP layer
  tests/                concurrency, idempotency, tamper, RBAC, validation, invariants
  scripts/race_attack.py
frontend/
  src/components/Statement.tsx   ledger-style statement with hash seals
  src/components/MoveMoney.tsx   stable idempotency key per intended operation
  src/components/Integrity.tsx   run and inspect invariant proofs
  src/views/                     holder / admin / auditor views
```

## What I'd add next

- Reversals as compensating transactions (`reverses_transaction_id`), so admins can correct mistakes without touching history
- An expiry/retention job for old idempotency records (archive, never delete in place)
- Periodic anchoring of each account's latest hash to an external store, so even a full database rewrite is detectable
- Multi-currency with explicit FX legs
