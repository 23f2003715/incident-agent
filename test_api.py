from fastapi.testclient import TestClient

from app.main import app


def test_idempotency_and_approval_lifecycle(monkeypatch):
    async def plan(*_args, **_kwargs):
        return {"summary": "test", "suspected_cause": "test", "recommended_action": "restart", "requires_approval": False, "source": "test"}

    monkeypatch.setattr("app.main.build_plan", plan)
    body = {"incident_id": "INC-1", "title": "Checkout down", "description": "5xx spike", "severity": "critical", "context": {"api_key": "do-not-store"}}
    with TestClient(app) as client:
        first = client.post("/v2/incidents", json=body, headers={"Idempotency-Key": "unique-test-key", "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"})
        assert first.status_code == 202
        run = first.json()
        assert run["state"] == "AWAITING_APPROVAL"
        assert "do-not-store" not in str(run)
        assert client.post("/v2/incidents", json=body, headers={"Idempotency-Key": "unique-test-key"}).json()["run_id"] == run["run_id"]
        approval = client.post(f"/v2/incidents/{run['run_id']}/receipts", json={"receipt_id": "approval-1", "receipt_type": "approval", "command_id": run["pending_command"]["command_id"], "status": "approved", "actor": "on-call"})
        assert approval.status_code == 200
        action_command = approval.json()["pending_command"]
        done = client.post(f"/v2/incidents/{run['run_id']}/receipts", json={"receipt_id": "action-1", "receipt_type": "action", "command_id": action_command["command_id"], "status": "succeeded", "actor": "runner"})
        assert done.json()["state"] == "COMPLETED"
