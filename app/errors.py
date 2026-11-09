from typing import Any


class ApiError(Exception):
    """An error returned to the client as {"error": {code, message, details}}."""

    def __init__(self, status_code: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


class Refused(Exception):
    """A business refusal raised inside a money-movement handler.

    The idempotency engine catches it, writes a refusal_audit_logs row in the
    same transaction, and caches the error response under the idempotency key
    so a retry gets the same answer.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict | None = None,
        source_account_id: Any = None,
        destination_account_id: Any = None,
        requested_amount: int | None = None,
        balance_snapshot: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.source_account_id = source_account_id
        self.destination_account_id = destination_account_id
        self.requested_amount = requested_amount
        self.balance_snapshot = balance_snapshot


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


# Reason codes used in refusal_audit_logs and error responses.
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
UNAUTHORIZED = "UNAUTHORIZED"
UNAUTHENTICATED = "UNAUTHENTICATED"
FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
CONCURRENT_CONFLICT = "CONCURRENT_CONFLICT"
IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
MISSING_IDEMPOTENCY_KEY = "MISSING_IDEMPOTENCY_KEY"
INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"
ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
ACCOUNT_FROZEN = "ACCOUNT_FROZEN"
SAME_ACCOUNT = "SAME_ACCOUNT"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
VALIDATION_ERROR = "VALIDATION_ERROR"
LOGIN_FAILED = "LOGIN_FAILED"
INVARIANT_GUARD_TRIPPED = "INVARIANT_GUARD_TRIPPED"
