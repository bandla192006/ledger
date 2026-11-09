-- =============================================================================
-- Least-privilege application role.
--
-- The API connects as `ledgerline_app`, which has NO UPDATE / DELETE / TRUNCATE
-- privilege on any append-only table. So tampering is blocked twice: first by
-- missing privileges ("permission denied"), then by the triggers in schema.sql
-- (which also stop the table owner / superuser).
--
-- Run after schema.sql. Change the password outside local development.
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgerline_app') THEN
        CREATE ROLE ledgerline_app LOGIN PASSWORD 'ledgerline_app';
    END IF;
END
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ledgerline_app;
GRANT USAGE ON SCHEMA public TO ledgerline_app;

-- Read everything.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ledgerline_app;

-- Append to everything.
GRANT INSERT ON users, accounts, transactions, ledger_entries, idempotency_keys,
                refusal_audit_logs, anomaly_flags, invariant_runs
      TO ledgerline_app;

-- The only mutable columns in the whole system.
GRANT UPDATE (balance, status, name) ON accounts TO ledgerline_app;
GRANT UPDATE (status, response_code, response_body, transaction_id, completed_at)
      ON idempotency_keys TO ledgerline_app;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO ledgerline_app;
