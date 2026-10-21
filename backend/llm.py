"""Minimal OpenRouter client: one function that returns validated JSON.

OpenRouter speaks the OpenAI chat-completions format. We ask for JSON that
follows a schema (`response_format: json_schema`); if a provider rejects that
parameter, we retry once without it and rely on the prompt. Either way the
caller validates what comes back. Nothing here trusts the model's output.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from config import LLM_MODEL, OPENROUTER_API_KEY

API_URL = "https://openrouter.ai/api/v1/chat/completions"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LLMError(RuntimeError):
    pass


def complete_json(system: str, user: str, schema: dict[str, Any], schema_name: str,
                  *, model: str | None = None, max_tokens: int = 8000) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (parsed JSON, metadata: model and token usage)."""
    if not OPENROUTER_API_KEY:
        raise LLMError("OPENROUTER_API_KEY is not set. Add it to .env, or use the cached result.")
    body: dict[str, Any] = {
        "model": model or LLM_MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
    }
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "X-Title": "Basis Trail (synthetic demo)"}

    with httpx.Client(timeout=180) as client:
        resp = client.post(API_URL, headers=headers, json=body)
        if resp.status_code == 400 and "response_format" in resp.text:
            body.pop("response_format")  # provider doesn't support it: prompt-only JSON
            resp = client.post(API_URL, headers=headers, json=body)
    if resp.status_code != 200:
        raise LLMError(f"OpenRouter {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"unexpected response shape: {json.dumps(data)[:500]}") from None
    try:
        parsed = json.loads(_FENCE.sub("", content.strip()))
    except json.JSONDecodeError as exc:
        raise LLMError(f"model did not return valid JSON ({exc}): {content[:300]}") from None
    meta = {"model": data.get("model", body["model"]), "usage": data.get("usage", {})}
    return parsed, meta