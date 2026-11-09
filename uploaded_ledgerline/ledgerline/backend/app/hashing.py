import hashlib
import json
from datetime import datetime, timedelta, timezone

GENESIS_HASH = "0" * 64
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def request_fingerprint(method: str, path: str, body: dict) -> str:
    """SHA-256 of a canonical JSON form of the request, used to detect key reuse."""
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _epoch_micros(ts: datetime) -> int:
    # Integer arithmetic only — float timestamps would drift by a microsecond.
    return (ts - _EPOCH) // timedelta(microseconds=1)


def ledger_entry_hash(
    prev_hash: str,
    entry_id,
    transaction_id,
    account_id,
    account_seq: int,
    entry_type: str,
    amount: int,
    balance_after: int,
    currency: str,
    created_at: datetime,
) -> str:
    """Independent re-implementation of SQL `ledger_entry_hash()` in schema.sql.

    The verifier uses this instead of trusting the database function, so a
    tampered function cannot vouch for tampered rows.
    """
    parts = [
        prev_hash,
        str(entry_id),
        str(transaction_id),
        str(account_id),
        str(account_seq),
        entry_type,
        str(amount),
        str(balance_after),
        currency.strip(),
        str(_epoch_micros(created_at)),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
