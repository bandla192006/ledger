from fastapi import APIRouter, Depends, Query
from psycopg.types.json import Jsonb
from fastapi.encoders import jsonable_encoder

from ..db import Database, fetchall, fetchone
from ..invariants import run_invariant_checks
from ..schemas import AnomalyRequest
from ..security import Principal, get_db, require_roles

router = APIRouter(tags=["audit"])

readers = require_roles("ADMIN", "AUDITOR")


@router.get("/audit/refusals")
async def refusal_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    reason_code: str | None = Query(None, max_length=64),
    principal: Principal = Depends(readers),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT r.id, r.attempted_by, u.email AS attempted_by_email, r.actor_role, r.action, r.reason_code,
                      r.http_status, r.request_path, r.idempotency_key, r.source_account_id,
                      r.destination_account_id, r.requested_amount, r.account_balance_snapshot, r.details,
                      r.created_at
                 FROM refusal_audit_logs r LEFT JOIN users u ON u.id = r.attempted_by
                WHERE (%s::text IS NULL OR r.reason_code = %s::text)
                ORDER BY r.id DESC LIMIT %s OFFSET %s""",
            [reason_code, reason_code, limit, offset],
        )
        counts = await fetchall(
            conn, "SELECT reason_code, count(*) AS n FROM refusal_audit_logs GROUP BY reason_code ORDER BY n DESC"
        )
    return {"refusals": rows, "counts": counts, "limit": limit, "offset": offset}


@router.get("/audit/idempotency-keys")
async def idempotency_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    principal: Principal = Depends(readers),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT k.user_id, u.email AS user_email, k.idempotency_key, k.request_method, k.request_path,
                      k.request_hash, k.status, k.response_code, k.response_body, k.transaction_id,
                      k.created_at, k.completed_at
                 FROM idempotency_keys k JOIN users u ON u.id = k.user_id
                ORDER BY k.created_at DESC LIMIT %s OFFSET %s""",
            [limit, offset],
        )
    return {"idempotency_keys": rows, "limit": limit, "offset": offset}


@router.post("/invariants/verify")
async def verify_invariants(principal: Principal = Depends(readers), db: Database = Depends(get_db)):
    async with db.pool.connection() as conn:
        report = await run_invariant_checks(conn)
    report = jsonable_encoder(report)
    async with db.pool.connection() as conn:
        async with conn.transaction():
            run = await fetchone(
                conn,
                "INSERT INTO invariant_runs (run_by, passed, results) VALUES (%s, %s, %s) RETURNING id, created_at",
                [principal.id, report["passed"], Jsonb(report)],
            )
    return {"run_id": run["id"], "run_at": run["created_at"], **report}


@router.get("/invariants/runs")
async def invariant_runs(
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(readers),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT r.id, r.passed, r.created_at, u.email AS run_by_email, r.results
                 FROM invariant_runs r JOIN users u ON u.id = r.run_by
                ORDER BY r.id DESC LIMIT %s""",
            [limit],
        )
    return {"runs": rows}


@router.post("/anomalies", status_code=201)
async def flag_anomaly(
    req: AnomalyRequest,
    principal: Principal = Depends(require_roles("ADMIN")),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        async with conn.transaction():
            row = await fetchone(
                conn,
                """INSERT INTO anomaly_flags (target_type, target_id, severity, reason, flagged_by)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, target_type, target_id, severity, reason, flagged_by, created_at""",
                [req.target_type, req.target_id, req.severity, req.reason, principal.id],
            )
    return {"anomaly": row}


@router.get("/anomalies")
async def list_anomalies(
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(readers),
    db: Database = Depends(get_db),
):
    async with db.pool.connection() as conn:
        rows = await fetchall(
            conn,
            """SELECT f.id, f.target_type, f.target_id, f.severity, f.reason, u.email AS flagged_by_email,
                      f.created_at
                 FROM anomaly_flags f JOIN users u ON u.id = f.flagged_by
                ORDER BY f.id DESC LIMIT %s""",
            [limit],
        )
    return {"anomalies": rows}
