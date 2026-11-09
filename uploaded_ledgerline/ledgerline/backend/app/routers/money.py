from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from .. import money
from ..db import Database
from ..idempotency import execute_idempotent, validate_key
from ..schemas import DepositRequest, TransferRequest, WithdrawalRequest
from ..security import Principal, get_db, require_roles

router = APIRouter(tags=["money movement"])

# Only account holders move money. Admins and auditors are deliberately excluded
# (separation of duties).
holder_only = require_roles("ACCOUNT_HOLDER")


def _respond(result) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if result.replayed else {}
    return JSONResponse(status_code=result.status_code, content=result.body, headers=headers)


@router.post("/deposits", status_code=201)
async def create_deposit(
    req: DepositRequest,
    request: Request,
    principal: Principal = Depends(holder_only),
    db: Database = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    path = request.url.path
    key = await validate_key(db, principal, idempotency_key, "DEPOSIT", path)
    result = await execute_idempotent(
        db, principal=principal, key=key, method="POST", path=path,
        payload=req.model_dump(mode="json"), action="DEPOSIT",
        handler=lambda conn: money.deposit(conn, principal, req, key, db.settlement_account_id),
    )
    return _respond(result)


@router.post("/withdrawals", status_code=201)
async def create_withdrawal(
    req: WithdrawalRequest,
    request: Request,
    principal: Principal = Depends(holder_only),
    db: Database = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    path = request.url.path
    key = await validate_key(db, principal, idempotency_key, "WITHDRAWAL", path)
    result = await execute_idempotent(
        db, principal=principal, key=key, method="POST", path=path,
        payload=req.model_dump(mode="json"), action="WITHDRAWAL",
        handler=lambda conn: money.withdraw(conn, principal, req, key, db.settlement_account_id),
    )
    return _respond(result)


@router.post("/transfers", status_code=201)
async def create_transfer(
    req: TransferRequest,
    request: Request,
    principal: Principal = Depends(holder_only),
    db: Database = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    path = request.url.path
    key = await validate_key(db, principal, idempotency_key, "TRANSFER", path)
    result = await execute_idempotent(
        db, principal=principal, key=key, method="POST", path=path,
        payload=req.model_dump(mode="json"), action="TRANSFER",
        handler=lambda conn: money.transfer(conn, principal, req, key),
    )
    return _respond(result)
