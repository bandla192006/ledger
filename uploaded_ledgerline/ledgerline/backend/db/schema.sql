-- =============================================================================
-- LedgerLine — double-entry ledger schema (PostgreSQL 14+)
--
-- The database is the last line of defence. Every money invariant the API
-- promises is ALSO enforced here, so a bug in application code (or a person
-- with a psql prompt) cannot silently create, destroy or rewrite money:
--
--   I1  amounts are integer minor units (paise/cents), always > 0
--   I2  user accounts can never go negative           (CHECK constraint)
--   I3  every transaction is balanced: sum(DEBIT) = sum(CREDIT) = amount,
--       with at least two legs                      (deferred constraint trigger)
--   I4  an account's cached balance always equals the balance_after of its
--       latest ledger entry                         (deferred constraint triggers)
--   I5  balance_after is a continuous running total per account
--   I6  ledger rows are append-only: UPDATE / DELETE / TRUNCATE raise
--   I7  each account's entries form a SHA-256 hash chain, so even an edit made
--       with triggers disabled is detected by the verifier
--   I8  one posting per (user, idempotency key)       (UNIQUE constraint)
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Shared guard: raises on any attempt to modify an append-only table.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION prevent_ledger_tampering() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Ledger entries are immutable and append-only. % on % is forbidden.',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'P0001', HINT = 'Post a new compensating transaction instead.';
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- Users & roles
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email          TEXT NOT NULL,
    full_name      TEXT NOT NULL CHECK (length(full_name) BETWEEN 1 AND 120),
    role           TEXT NOT NULL CHECK (role IN ('ADMIN', 'ACCOUNT_HOLDER', 'AUDITOR')),
    password_hash  TEXT NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_email_format CHECK (email ~ '^[^@\s]+@[^@\s]+$')
);
CREATE UNIQUE INDEX users_email_lower_uq ON users (lower(email));

-- -----------------------------------------------------------------------------
-- Accounts
--   USER accounts belong to an ACCOUNT_HOLDER and can never be negative.
--   SYSTEM accounts (the Settlement/Clearing account) mirror money that
--   entered/left the system, so their balance is the negative of net deposits.
-- -----------------------------------------------------------------------------
CREATE TABLE accounts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      UUID REFERENCES users (id),
    name          TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    account_type  TEXT NOT NULL CHECK (account_type IN ('USER', 'SYSTEM')),
    system_code   TEXT UNIQUE,
    currency      CHAR(3) NOT NULL DEFAULT 'INR' CHECK (currency ~ '^[A-Z]{3}$'),
    balance       BIGINT NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'FROZEN')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- I2: no overdrafts. Only the system clearing account is authorised to go below zero.
    CONSTRAINT accounts_balance_non_negative CHECK (account_type = 'SYSTEM' OR balance >= 0),
    -- Settlement = -(deposits - withdrawals) and user balances are >= 0, so it can never be positive.
    CONSTRAINT accounts_system_balance_non_positive CHECK (account_type = 'USER' OR balance <= 0),
    CONSTRAINT accounts_owner_matches_type CHECK ((account_type = 'SYSTEM') = (owner_id IS NULL)),
    CONSTRAINT accounts_system_code_matches_type CHECK ((account_type = 'SYSTEM') = (system_code IS NOT NULL))
);
CREATE INDEX accounts_owner_idx ON accounts (owner_id);

-- Identity columns of an account can never change, and accounts are never deleted.
CREATE OR REPLACE FUNCTION accounts_guard_identity() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Accounts cannot be deleted; freeze them instead.';
    END IF;
    IF NEW.id <> OLD.id
       OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
       OR NEW.account_type <> OLD.account_type
       OR NEW.system_code IS DISTINCT FROM OLD.system_code
       OR NEW.currency <> OLD.currency
       OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'Account identity columns are immutable.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER accounts_guard_identity_trg
    BEFORE UPDATE OR DELETE ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_guard_identity();

CREATE TRIGGER accounts_no_truncate_trg
    BEFORE TRUNCATE ON accounts
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- -----------------------------------------------------------------------------
-- Transactions (the business event) — append-only
-- -----------------------------------------------------------------------------
CREATE TABLE transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type             TEXT NOT NULL CHECK (type IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER')),
    amount           BIGINT NOT NULL CHECK (amount > 0),
    currency         CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    initiated_by     UUID NOT NULL REFERENCES users (id),
    idempotency_key  TEXT NOT NULL,
    description      TEXT CHECK (length(description) <= 280),
    external_ref     TEXT CHECK (length(external_ref) <= 120),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- I8: even if the idempotency layer were bypassed, a key can post only once.
    CONSTRAINT transactions_idempotency_uq UNIQUE (initiated_by, idempotency_key)
);
CREATE INDEX transactions_created_idx ON transactions (created_at DESC);

CREATE TRIGGER transactions_append_only_trg
    BEFORE UPDATE OR DELETE ON transactions
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();
CREATE TRIGGER transactions_no_truncate_trg
    BEFORE TRUNCATE ON transactions
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- -----------------------------------------------------------------------------
-- Ledger entries (the postings) — append-only, hash-chained per account
-- -----------------------------------------------------------------------------
CREATE TABLE ledger_entries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID NOT NULL REFERENCES transactions (id),
    account_id      UUID NOT NULL REFERENCES accounts (id),
    account_seq     BIGINT NOT NULL,                       -- 1, 2, 3 ... per account (set by trigger)
    entry_type      TEXT NOT NULL CHECK (entry_type IN ('DEBIT', 'CREDIT')),
    amount          BIGINT NOT NULL CHECK (amount > 0),    -- I1
    balance_after   BIGINT NOT NULL,
    currency        CHAR(3) NOT NULL,
    prev_hash       CHAR(64) NOT NULL,                     -- set by trigger
    entry_hash      CHAR(64) NOT NULL,                     -- set by trigger
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),    -- set by trigger
    CONSTRAINT ledger_entries_account_seq_uq UNIQUE (account_id, account_seq),
    CONSTRAINT ledger_entries_one_leg_per_account UNIQUE (transaction_id, account_id)
);
CREATE INDEX ledger_entries_tx_idx ON ledger_entries (transaction_id);
CREATE INDEX ledger_entries_created_idx ON ledger_entries (created_at DESC);

-- Canonical hash of one entry. The Python verifier recomputes this
-- independently (app/hashing.py) — keep the two in sync.
CREATE OR REPLACE FUNCTION ledger_entry_hash(
    p_prev_hash TEXT, p_id UUID, p_transaction_id UUID, p_account_id UUID,
    p_account_seq BIGINT, p_entry_type TEXT, p_amount BIGINT, p_balance_after BIGINT,
    p_currency TEXT, p_created_at TIMESTAMPTZ
) RETURNS TEXT AS $$
    SELECT encode(sha256(convert_to(concat_ws('|',
        p_prev_hash, p_id::text, p_transaction_id::text, p_account_id::text,
        p_account_seq::text, p_entry_type, p_amount::text, p_balance_after::text,
        p_currency, (extract(epoch FROM p_created_at) * 1000000)::bigint::text
    ), 'UTF8')), 'hex');
$$ LANGUAGE sql IMMUTABLE;

-- BEFORE INSERT: the DB, not the caller, decides sequence number, timestamp and
-- hashes, and it re-checks the running balance (I5).
CREATE OR REPLACE FUNCTION ledger_entries_before_insert() RETURNS TRIGGER AS $$
DECLARE
    v_currency      CHAR(3);
    v_last_seq      BIGINT;
    v_last_balance  BIGINT;
    v_last_hash     CHAR(64);
    v_expected      BIGINT;
BEGIN
    -- Serialise all postings to this account (a no-op if the caller already holds the lock).
    SELECT currency INTO v_currency FROM accounts WHERE id = NEW.account_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Ledger entry references unknown account %', NEW.account_id;
    END IF;
    IF NEW.currency <> v_currency THEN
        RAISE EXCEPTION 'Entry currency % does not match account currency %', NEW.currency, v_currency;
    END IF;

    SELECT account_seq, balance_after, entry_hash
      INTO v_last_seq, v_last_balance, v_last_hash
      FROM ledger_entries
     WHERE account_id = NEW.account_id
     ORDER BY account_seq DESC
     LIMIT 1;

    IF NOT FOUND THEN
        v_last_seq := 0;
        v_last_balance := 0;
        v_last_hash := repeat('0', 64);
    END IF;

    v_expected := v_last_balance + CASE WHEN NEW.entry_type = 'CREDIT' THEN NEW.amount ELSE -NEW.amount END;
    IF NEW.balance_after <> v_expected THEN
        RAISE EXCEPTION 'balance_after % breaks the running balance for account % (expected %)',
            NEW.balance_after, NEW.account_id, v_expected;
    END IF;

    NEW.account_seq := v_last_seq + 1;
    NEW.created_at  := now();
    NEW.prev_hash   := v_last_hash;
    NEW.entry_hash  := ledger_entry_hash(NEW.prev_hash, NEW.id, NEW.transaction_id, NEW.account_id,
                                         NEW.account_seq, NEW.entry_type, NEW.amount,
                                         NEW.balance_after, NEW.currency, NEW.created_at);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_entries_before_insert_trg
    BEFORE INSERT ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION ledger_entries_before_insert();

-- I6: append-only.
CREATE TRIGGER ledger_entries_append_only_trg
    BEFORE UPDATE OR DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();
CREATE TRIGGER ledger_entries_no_truncate_trg
    BEFORE TRUNCATE ON ledger_entries
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- I3: checked at COMMIT, after every leg has been written.
CREATE OR REPLACE FUNCTION check_transaction_balanced() RETURNS TRIGGER AS $$
DECLARE
    v_tx_id    UUID;
    v_amount   BIGINT;
    v_debits   BIGINT;
    v_credits  BIGINT;
    v_legs     INT;
BEGIN
    IF TG_TABLE_NAME = 'transactions' THEN
        v_tx_id := NEW.id;
    ELSE
        v_tx_id := NEW.transaction_id;
    END IF;

    SELECT amount INTO v_amount FROM transactions WHERE id = v_tx_id;

    SELECT COALESCE(SUM(amount) FILTER (WHERE entry_type = 'DEBIT'), 0),
           COALESCE(SUM(amount) FILTER (WHERE entry_type = 'CREDIT'), 0),
           COUNT(*)
      INTO v_debits, v_credits, v_legs
      FROM ledger_entries
     WHERE transaction_id = v_tx_id;

    IF v_legs < 2 OR v_debits <> v_credits OR v_debits <> v_amount THEN
        RAISE EXCEPTION 'Unbalanced transaction %: legs=%, debits=%, credits=%, amount=%',
            v_tx_id, v_legs, v_debits, v_credits, v_amount;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transactions_balanced_trg
    AFTER INSERT ON transactions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();

CREATE CONSTRAINT TRIGGER ledger_entries_balanced_trg
    AFTER INSERT ON ledger_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_transaction_balanced();

-- I4: the cached balance can only move together with a ledger entry.
CREATE OR REPLACE FUNCTION check_account_balance_matches_ledger() RETURNS TRIGGER AS $$
DECLARE
    v_account_id  UUID;
    v_balance     BIGINT;
    v_ledger      BIGINT;
BEGIN
    IF TG_TABLE_NAME = 'accounts' THEN
        v_account_id := NEW.id;
    ELSE
        v_account_id := NEW.account_id;
    END IF;

    SELECT balance INTO v_balance FROM accounts WHERE id = v_account_id;
    SELECT balance_after INTO v_ledger
      FROM ledger_entries
     WHERE account_id = v_account_id
     ORDER BY account_seq DESC
     LIMIT 1;

    IF v_balance <> COALESCE(v_ledger, 0) THEN
        RAISE EXCEPTION 'Account % balance % does not match ledger balance %',
            v_account_id, v_balance, COALESCE(v_ledger, 0);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER accounts_balance_matches_ledger_trg
    AFTER INSERT OR UPDATE OF balance ON accounts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_account_balance_matches_ledger();

CREATE CONSTRAINT TRIGGER ledger_entries_balance_matches_account_trg
    AFTER INSERT ON ledger_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_account_balance_matches_ledger();

-- -----------------------------------------------------------------------------
-- Idempotency keys — scoped per user so one user's key can never replay
-- another user's response.
-- -----------------------------------------------------------------------------
CREATE TABLE idempotency_keys (
    user_id          UUID NOT NULL REFERENCES users (id),
    idempotency_key  TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 255),
    request_method   TEXT NOT NULL,
    request_path     TEXT NOT NULL,
    request_hash     CHAR(64) NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('IN_FLIGHT', 'COMPLETED')),
    response_code    INT,
    response_body    JSONB,
    transaction_id   UUID REFERENCES transactions (id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    PRIMARY KEY (user_id, idempotency_key),
    CONSTRAINT idempotency_completed_has_response
        CHECK (status = 'IN_FLIGHT' OR (response_code IS NOT NULL AND response_body IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at DESC);

-- A key may move IN_FLIGHT -> COMPLETED exactly once; after that it is frozen.
CREATE OR REPLACE FUNCTION idempotency_keys_guard() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Idempotency records cannot be deleted.';
    END IF;
    IF OLD.status = 'COMPLETED' THEN
        RAISE EXCEPTION 'Idempotency key % is completed and immutable.', OLD.idempotency_key;
    END IF;
    IF NEW.user_id <> OLD.user_id OR NEW.idempotency_key <> OLD.idempotency_key
       OR NEW.request_hash <> OLD.request_hash OR NEW.request_path <> OLD.request_path
       OR NEW.request_method <> OLD.request_method OR NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'Idempotency request fingerprint is immutable.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER idempotency_keys_guard_trg
    BEFORE UPDATE OR DELETE ON idempotency_keys
    FOR EACH ROW EXECUTE FUNCTION idempotency_keys_guard();
CREATE TRIGGER idempotency_keys_no_truncate_trg
    BEFORE TRUNCATE ON idempotency_keys
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- -----------------------------------------------------------------------------
-- Refusal audit log — every rejected attempt, append-only
-- -----------------------------------------------------------------------------
CREATE TABLE refusal_audit_logs (
    id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempted_by              UUID REFERENCES users (id),
    actor_role                TEXT,
    action                    TEXT NOT NULL,
    reason_code               TEXT NOT NULL,
    http_status               INT NOT NULL,
    request_path              TEXT,
    idempotency_key           TEXT,
    source_account_id         UUID,            -- no FK: refusals may name accounts that don't exist
    destination_account_id    UUID,
    requested_amount          BIGINT,
    account_balance_snapshot  BIGINT,          -- balance of the account that would have been debited
    details                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX refusal_audit_logs_created_idx ON refusal_audit_logs (created_at DESC);
CREATE INDEX refusal_audit_logs_actor_idx ON refusal_audit_logs (attempted_by);
CREATE INDEX refusal_audit_logs_reason_idx ON refusal_audit_logs (reason_code);

CREATE TRIGGER refusal_audit_logs_append_only_trg
    BEFORE UPDATE OR DELETE ON refusal_audit_logs
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();
CREATE TRIGGER refusal_audit_logs_no_truncate_trg
    BEFORE TRUNCATE ON refusal_audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION prevent_ledger_tampering();

-- -----------------------------------------------------------------------------
-- Anomaly flags (ADMIN) and invariant verification runs (ADMIN / AUDITOR)
-- -----------------------------------------------------------------------------
CREATE TABLE anomaly_flags (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_type  TEXT NOT NULL CHECK (target_type IN ('ACCOUNT', 'TRANSACTION', 'USER', 'LEDGER_ENTRY')),
    target_id    UUID NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
    reason       TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    flagged_by   UUID NOT NULL REFERENCES users (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER anomaly_flags_append_only_trg
    BEFORE UPDATE OR DELETE ON anomaly_flags
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();

CREATE TABLE invariant_runs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_by      UUID NOT NULL REFERENCES users (id),
    passed      BOOLEAN NOT NULL,
    results     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER invariant_runs_append_only_trg
    BEFORE UPDATE OR DELETE ON invariant_runs
    FOR EACH ROW EXECUTE FUNCTION prevent_ledger_tampering();

-- -----------------------------------------------------------------------------
-- The one system clearing account. Deposits DEBIT it, withdrawals CREDIT it.
-- -----------------------------------------------------------------------------
INSERT INTO accounts (name, account_type, system_code, currency)
VALUES ('Settlement / Clearing', 'SYSTEM', 'SETTLEMENT', 'INR');

COMMIT;
