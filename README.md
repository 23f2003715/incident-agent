# Observable Incident Agent

A receipt-driven FastAPI incident workflow service. It plans with Ollama by default (`llama3.1:8b`) but does not treat an LLM response as a completed action. Every remediation completes only when an idempotent action receipt arrives.

## Run locally

Use Python 3.11.

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
ollama pull llama3.1:8b
uvicorn app.main:app --reload
```

The service starts at `http://127.0.0.1:8000`; OpenAPI is at `/docs`. If Ollama is unavailable or returns 503 after retries, the service records a conservative fallback plan instead of failing the incident submission.

```bash
pytest
docker build -t observable-incident-agent .
docker run -p 8000:8000 -e OLLAMA_URL=http://host.docker.internal:11434 observable-incident-agent
```

## API contract

`POST /v2/incidents` requires `Idempotency-Key` (or `idempotency_key` in the body). Reusing it returns the original run rather than creating another workflow. Send an optional W3C `traceparent`; malformed/missing values cause a new trace to be created.

```json
{
  "incident_id": "INC-123",
  "title": "Checkout 5xx spike",
  "description": "Error rate is 38%.",
  "severity": "critical",
  "service": "checkout",
  "context": {"region": "ap-south-1"}
}
```

The `202` response includes `run_id`, a redacted plan, state, `traceparent`, and `pending_command`. High/critical incidents always require approval; other incidents require it if the model recommends it.

`POST /v2/incidents/{runId}/receipts` records exactly-once receipts. `receipt_id` is unique per run; safely resend the same receipt after a network failure. The `command_id` must match the pending command.

```json
{
  "receipt_id": "approval-0001",
  "receipt_type": "approval",
  "command_id": "<pending command id>",
  "status": "approved",
  "actor": "on-call@example.com",
  "details": {"ticket": "CHG-44"}
}
```

Approval statuses are `approved` or `rejected`; action statuses are `succeeded` or `failed`. `GET /v2/incidents/{runId}` returns the durable workflow view and all event-specific OTLP JSON payloads.

## State machine

```
ANALYZING → AWAITING_APPROVAL → AWAITING_ACTION_RECEIPT → COMPLETED
              │                    └────────────────────→ FAILED
              ├─────────────────────────────────────────→ REJECTED
              └─────────────────────────────────────────→ TIMED_OUT
ANALYZING → AWAITING_ACTION_RECEIPT  (low-risk plan)
```

The approval deadline is `APPROVAL_TIMEOUT_SECONDS` (900 by default) and is evaluated whenever the run is read or a receipt arrives. Terminal runs reject new receipt IDs.

## Safety, retries, and observability

- Model calls have a configurable timeout (`OLLAMA_TIMEOUT_SECONDS`, default 20) and retry HTTP 503 with exponential backoff up to `OLLAMA_MAX_RETRIES` (default 3).
- `traceparent` is parsed at ingress, a child W3C trace context is propagated to Ollama and commands, and every lifecycle event stores OTLP JSON (`resourceSpans`) in SQLite.
- Keys matching password, secret, token, authorization, API key, cookie, email, or phone are replaced with `[REDACTED]` before storage, OTLP export, or API response.
- SQLite data is configured with `DATABASE_URL` (default `sqlite:///./incident-agent.db`). Use persistent disk and a managed database for multi-instance production deployments.

`render.yaml` is included for a Docker-based Render deployment. Render does not host a local Ollama process, so point `OLLAMA_URL` at an accessible Ollama service before deploying.
