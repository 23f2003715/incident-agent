from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class IncidentCreate(BaseModel):
    incident_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=10000)
    severity: Literal["low", "medium", "high", "critical"]
    service: str | None = Field(default=None, max_length=255)
    context: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=255)


class ReceiptCreate(BaseModel):
    receipt_id: str = Field(min_length=1, max_length=255)
    receipt_type: Literal["approval", "action"]
    command_id: str | None = Field(default=None, max_length=255)
    status: Literal["approved", "rejected", "succeeded", "failed"]
    actor: str = Field(min_length=1, max_length=255)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def receipt_matches_type(self):
        allowed = {"approval": {"approved", "rejected"}, "action": {"succeeded", "failed"}}
        if self.status not in allowed[self.receipt_type]:
            raise ValueError("status is not valid for receipt_type")
        return self
