from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from .config import settings
from .observability import redact


async def build_plan(incident: dict[str, Any], traceparent: str) -> dict[str, Any]:
    """Ask Ollama for an advisory plan; never allow it to execute an action."""
    prompt = """You are an incident-analysis assistant. Return only JSON with keys summary, suspected_cause,
recommended_action, requires_approval (boolean). This is advisory only; never claim an action was executed.\nIncident:\n""" + json.dumps(redact(incident))
    payload = {"model": settings.ollama_model, "prompt": prompt, "stream": False, "format": "json"}
    for attempt in range(settings.ollama_max_retries):
        try:
            async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
                response = await client.post(f"{settings.ollama_url.rstrip('/')}/api/generate", json=payload, headers={"traceparent": traceparent})
            if response.status_code == 503 and attempt + 1 < settings.ollama_max_retries:
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            response.raise_for_status()
            data = json.loads(response.json()["response"])
            return {"summary": str(data.get("summary", "Analysis completed.")), "suspected_cause": str(data.get("suspected_cause", "Unknown")), "recommended_action": str(data.get("recommended_action", "Investigate service health.")), "requires_approval": bool(data.get("requires_approval", False)), "source": "ollama"}
        except (httpx.TimeoutException, httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as exc:
            if attempt + 1 == settings.ollama_max_retries:
                return fallback_plan(incident, str(exc))
            await asyncio.sleep(0.25 * (2**attempt))
    return fallback_plan(incident, "unreachable")


def fallback_plan(incident: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"summary": "LLM analysis unavailable; conservative workflow selected.", "suspected_cause": "Unavailable", "recommended_action": "Collect diagnostics and investigate.", "requires_approval": incident["severity"] in {"high", "critical"}, "source": "fallback", "llm_error": reason}
