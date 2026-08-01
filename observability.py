from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Any

SENSITIVE = re.compile(r"(password|secret|token|authorization|api[_-]?key|cookie|email|phone)", re.I)
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def parse_traceparent(value: str | None) -> tuple[str, str | None, str]:
    match = TRACEPARENT.fullmatch(value or "")
    if match:
        return match.group(1), match.group(2), match.group(3)
    return secrets.token_hex(16), None, "01"


def child_traceparent(trace_id: str, flags: str) -> str:
    return f"00-{trace_id}-{secrets.token_hex(8)}-{flags}"


def otlp_event(run_id: str, trace_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    span_id = secrets.token_hex(8)
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    attrs = [{"key": "incident.run_id", "value": {"stringValue": run_id}}, {"key": "event.type", "value": {"stringValue": event_type}}]
    span = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": event_type,
        "kind": 1,
        "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano": str(now_ns),
        "attributes": attrs,
        "events": [{"timeUnixNano": str(now_ns), "name": event_type, "attributes": [{"key": "payload.sha256", "value": {"stringValue": hashlib.sha256(str(payload).encode()).hexdigest()}}]}],
    }
    return {
        "resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "observable-incident-agent"}}]},
            "scopeSpans": [{"scope": {"name": "observable-incident-agent"}, "spans": [span]}],
        }]
    }
