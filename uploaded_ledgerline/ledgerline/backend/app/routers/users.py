from fastapi import APIRouter, Depends, Request
from psycopg import errors as pg_errors
from starlette.concurrency import run_in_threadpool

from .. import errors
from ..db import Database, fetchall, fetchone
from ..errors import ApiError
from ..schemas import CreateUserRequest
from ..security import Principal, get_db, hash_password, require_roles

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(
    principal: Principal = Depends(require_roles("ADMIN", "AUDITOR")),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT u.id, u.email, u.full_name, u.role, u.is_active, u.created_at,
                      (SELECT count(*) FROM accounts a WHERE a.owner_id = u.id) AS account_count
                 FROM users u ORDER BY u.role, u.full_name""",
        )
    return {"users": rows}


@router.post("", status_code=201)
async def create_user(
    req: CreateUserRequest,
    request: Request,
    principal: Principal = Depends(require_roles("ADMIN")),
    db: Database = Depends(get_db),
):
    iterations = request.app.state.settings.password_hash_iterations
    pw_hash = await run_in_threadpool(hash_password, req.password, iterations)
    try:
        async with db.pool.connection() as conn:
            async with conn.transaction():
                row = await fetchone(
                    conn,
                    """INSERT INTO users (email, full_name, role, password_hash)
                       VALUES (%s, %s, %s, %s)
                       RETURNING id, email, full_name, role, is_active, created_at""",
                    [req.email, req.full_name, req.role, pw_hash],
                )
    except pg_errors.UniqueViolation:
        raise ApiError(409, "EMAIL_TAKEN", "A user with this email already exists.")
    return {"user": row}
