from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, engine, get_session
from .models import IncidentRun, Receipt, RunEvent, utcnow
from .observability import child_traceparent, otlp_event, parse_traceparent, redact
from .ollama import build_plan
from .schemas import IncidentCreate, ReceiptCreate

app = FastAPI(title="Observable Incident Agent", version="2.0.0")


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def emit_event(session: Session, run: IncidentRun, event_type: str, payload: dict[str, Any]) -> None:
    safe = redact(payload)
    session.add(RunEvent(run_id=run.id, event_type=event_type, payload_json=safe, otlp_json=otlp_event(run.id, run.trace_id, event_type, safe)))


def command(kind: str, run: IncidentRun) -> dict[str, Any]:
    return {"command_id": str(uuid4()), "type": kind, "run_id": run.id, "traceparent": child_traceparent(run.trace_id, run.trace_flags), "instructions": "Record a receipt after this operation. No operation is assumed successful without that receipt."}


def apply_timeout(session: Session, run: IncidentRun) -> None:
    if run.state == "AWAITING_APPROVAL" and run.approval_expires_at and utcnow() >= run.approval_expires_at:
        run.state, run.error = "TIMED_OUT", "Approval receipt was not received before its deadline."
        run.current_command_json = None
        emit_event(session, run, "run.timed_out", {"reason": run.error})


def representation(session: Session, run: IncidentRun) -> dict[str, Any]:
    apply_timeout(session, run)
    events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.created_at)).all()
    return {"run_id": run.id, "incident_id": run.incident_id, "state": run.state, "severity": run.severity, "traceparent": child_traceparent(run.trace_id, run.trace_flags), "plan": run.plan_json, "pending_command": run.current_command_json, "approval_expires_at": run.approval_expires_at.isoformat() if run.approval_expires_at else None, "error": run.error, "events": [{"type": event.event_type, "at": event.created_at.isoformat(), "payload": event.payload_json, "otlp": event.otlp_json} for event in events]}


@app.post("/v2/incidents", status_code=status.HTTP_202_ACCEPTED)
async def create_incident(
    incident: IncidentCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    key = idempotency_key or incident.idempotency_key
    if not key:
        raise HTTPException(400, "Idempotency-Key header (or idempotency_key body field) is required")
    existing = session.scalar(select(IncidentRun).where(IncidentRun.idempotency_key == key))
    if existing:
        response.status_code = status.HTTP_200_OK
        return representation(session, existing)
    trace_id, parent_span_id, flags = parse_traceparent(request.headers.get("traceparent"))
    payload = incident.model_dump()
    run = IncidentRun(idempotency_key=key, incident_id=incident.incident_id, severity=incident.severity, state="ANALYZING", trace_id=trace_id, parent_span_id=parent_span_id, trace_flags=flags, input_json=redact(payload))
    session.add(run)
    session.flush()
    emit_event(session, run, "incident.received", payload)
    plan = await build_plan(payload, child_traceparent(trace_id, flags))
    run.plan_json = redact(plan)
    approval_required = incident.severity in {"high", "critical"} or plan["requires_approval"]
    if approval_required:
        run.state = "AWAITING_APPROVAL"
        run.approval_expires_at = utcnow() + timedelta(seconds=settings.approval_timeout_seconds)
        run.current_command_json = command("approval_required", run)
        emit_event(session, run, "approval.requested", {"plan": plan, "expires_at": run.approval_expires_at.isoformat()})
    else:
        run.state = "AWAITING_ACTION_RECEIPT"
        run.current_command_json = command("execute_remediation", run)
        emit_event(session, run, "action.requested", {"plan": plan})
    session.commit()
    return representation(session, run)


@app.post("/v2/incidents/{run_id}/receipts")
def record_receipt(run_id: str, receipt: ReceiptCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    run = session.get(IncidentRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    apply_timeout(session, run)
    duplicate = session.scalar(select(Receipt).where(Receipt.run_id == run_id, Receipt.receipt_id == receipt.receipt_id))
    if duplicate:
        session.commit()
        return representation(session, run)
    if run.state in {"COMPLETED", "FAILED", "REJECTED", "TIMED_OUT"}:
        raise HTTPException(409, f"Cannot accept receipt in terminal state {run.state}")
    pending = run.current_command_json or {}
    if receipt.command_id and receipt.command_id != pending.get("command_id"):
        raise HTTPException(409, "Receipt command_id does not match the pending command")
    if run.state == "AWAITING_APPROVAL" and receipt.receipt_type != "approval":
        raise HTTPException(409, "An approval receipt is required")
    if run.state == "AWAITING_ACTION_RECEIPT" and receipt.receipt_type != "action":
        raise HTTPException(409, "An action receipt is required")
    session.add(Receipt(run_id=run_id, receipt_id=receipt.receipt_id, receipt_json=redact(receipt.model_dump())))
    emit_event(session, run, "receipt.recorded", receipt.model_dump())
    if receipt.status == "approved":
        run.state = "AWAITING_ACTION_RECEIPT"
        run.approval_expires_at = None
        run.current_command_json = command("execute_remediation", run)
        emit_event(session, run, "action.requested", {"after_approval": True})
    elif receipt.status == "rejected":
        run.state, run.current_command_json = "REJECTED", None
        emit_event(session, run, "run.rejected", {"actor": receipt.actor})
    elif receipt.status == "succeeded":
        run.state, run.current_command_json = "COMPLETED", None
        emit_event(session, run, "run.completed", {"actor": receipt.actor})
    else:
        run.state, run.current_command_json, run.error = "FAILED", None, "Action receipt reported failure."
        emit_event(session, run, "run.failed", {"actor": receipt.actor, "details": receipt.details})
    session.commit()
    return representation(session, run)


@app.get("/v2/incidents/{run_id}")
def get_incident(run_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    run = session.get(IncidentRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    result = representation(session, run)
    session.commit()
    return result


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}
