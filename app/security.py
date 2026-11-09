import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import Depends, Request

from . import errors
from .db import Database, fetchone
from .errors import ApiError
from .refusals import log_refusal_standalone

ROLES = ("ADMIN", "ACCOUNT_HOLDER", "AUDITOR")


@dataclass(frozen=True)
class Principal:
    id: UUID
    email: str
    full_name: str
    role: str


# ---------------------------------------------------------------- passwords --

def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------- JWTs --

def create_access_token(user: dict, secret: str, ttl_minutes: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["sub", "exp", "role"]})


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def get_db(request: Request) -> Database:
    return request.app.state.db


async def get_current_user(request: Request, db: Database = Depends(get_db)) -> Principal:
    token = bearer_token(request)
    settings = request.app.state.settings
    if token is None:
        await _log_unauthenticated(db, request, "missing bearer token")
        raise ApiError(401, errors.UNAUTHENTICATED, "Authentication required.")
    try:
        claims = decode_token(token, settings.jwt_secret)
        user_id = UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        await _log_unauthenticated(db, request, "invalid or expired token")
        raise ApiError(401, errors.UNAUTHENTICATED, "Invalid or expired token.")

    # Always re-read the user: a deactivated user or changed role takes effect immediately.
    async with db.pool.connection() as conn:
        row = await fetchone(
            conn, "SELECT id, email, full_name, role, is_active FROM users WHERE id = %s", [user_id]
        )
    if row is None or not row["is_active"]:
        await _log_unauthenticated(db, request, "user missing or inactive")
        raise ApiError(401, errors.UNAUTHENTICATED, "User is not active.")
    return Principal(id=row["id"], email=row["email"], full_name=row["full_name"], role=row["role"])


async def _log_unauthenticated(db: Database, request: Request, why: str) -> None:
    await log_refusal_standalone(
        db,
        user_id=None,
        role=None,
        action=f"{request.method} {request.url.path}",
        reason_code=errors.UNAUTHENTICATED,
        http_status=401,
        request_path=request.url.path,
        details={"reason": why},
    )


def require_roles(*allowed: str):
    """Dependency factory: only the listed roles may call the route; denials are audited."""

    async def dependency(
        request: Request,
        principal: Principal = Depends(get_current_user),
        db: Database = Depends(get_db),
    ) -> Principal:
        if principal.role not in allowed:
            await log_refusal_standalone(
                db,
                user_id=principal.id,
                role=principal.role,
                action=f"{request.method} {request.url.path}",
                reason_code=errors.FORBIDDEN_ROLE,
                http_status=403,
                request_path=request.url.path,
                idempotency_key=request.headers.get("idempotency-key"),
                details={"allowed_roles": list(allowed)},
            )
            raise ApiError(
                403,
                errors.FORBIDDEN_ROLE,
                f"Role {principal.role} may not perform this action.",
                {"allowed_roles": list(allowed)},
            )
        return principal

    return dependency
