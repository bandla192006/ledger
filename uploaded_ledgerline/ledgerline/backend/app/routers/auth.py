from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from .. import errors
from ..db import Database, fetchone
from ..errors import ApiError
from ..refusals import log_refusal_standalone
from ..schemas import LoginRequest
from ..security import Principal, create_access_token, get_current_user, get_db, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@lru_cache(maxsize=4)
def _dummy_hash(iterations: int) -> str:
    """Verified against when the email is unknown, so response time doesn't reveal which emails exist."""
    return hash_password("not-a-real-password", iterations)


@router.post("/login")
async def login(req: LoginRequest, request: Request, db: Database = Depends(get_db)):
    async with db.pool.connection() as conn:
        user = await fetchone(
            conn,
            "SELECT id, email, full_name, role, password_hash, is_active FROM users WHERE lower(email) = lower(%s)",
            [req.email],
        )
    stored = user["password_hash"] if user else _dummy_hash(request.app.state.settings.password_hash_iterations)
    ok = await run_in_threadpool(verify_password, req.password, stored)
    if not user or not ok or not user["is_active"]:
        await log_refusal_standalone(
            db, user_id=user["id"] if user else None, role=user["role"] if user else None,
            action="LOGIN", reason_code=errors.LOGIN_FAILED, http_status=401, request_path=request.url.path,
            details={"email": req.email.lower()},
        )
        raise ApiError(401, errors.LOGIN_FAILED, "Invalid email or password.")

    settings = request.app.state.settings
    token = create_access_token(user, settings.jwt_secret, settings.jwt_ttl_minutes)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_ttl_minutes * 60,
        "user": {k: user[k] for k in ("id", "email", "full_name", "role")},
    }


@router.get("/me")
async def me(principal: Principal = Depends(get_current_user)):
    return {"id": principal.id, "email": principal.email, "full_name": principal.full_name, "role": principal.role}
