"""LLM wrapper (OpenAI SDK by default, Anthropic optional) with on-disk caching.

LLM_PROVIDER=openai    → OPENAI_API_KEY, OPENAI_MODEL / OPENAI_MODEL_FAST  (default)
LLM_PROVIDER=anthropic → ANTHROPIC_API_KEY, LLM_MODEL / LLM_MODEL_FAST

Every call is cached by (provider, model, system, user) so re-running a demo is free and
deterministic. `model=` accepts the logical tiers "default" / "fast" or a concrete model id.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from . import config
from .store import cache_get, cache_key, cache_put

_client = None
_client_lock = threading.Lock()
_JSON_RULE = "\n\nRespond with valid JSON only. No prose, no markdown fences."


class LLMUnavailable(RuntimeError):
    pass


def _resolve_model(model: str | None) -> str:
    openai = config.LLM_PROVIDER == "openai"
    if model in (None, "", "default"):
        return config.OPENAI_MODEL if openai else config.LLM_MODEL
    if model == "fast":
        return config.OPENAI_MODEL_FAST if openai else config.LLM_MODEL_FAST
    return model


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            if config.LLM_PROVIDER == "openai":
                from openai import OpenAI

                # explicit default: an empty OPENAI_BASE_URL in .env would otherwise reach the SDK as ""
                _client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL or "https://api.openai.com/v1")
            else:
                from anthropic import Anthropic

                _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        return _client


def _extract_json(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


def _openai(system: str, user: str, model: str, max_tokens: int, temperature: float | None) -> str:
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_completion_tokens": max_tokens,
    }
    # Reasoning models (gpt-5 family and later) reject non-default temperature and accept reasoning_effort.
    reasoning = re.match(r"^(gpt-5|gpt-6|o\d)", model) is not None
    if reasoning:
        kwargs["reasoning_effort"] = config.OPENAI_REASONING_EFFORT
    elif temperature is not None:
        kwargs["temperature"] = temperature
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # unknown param for this model → retry bare
        msg = str(exc).lower()
        if "reasoning_effort" in msg or "temperature" in msg or "unsupported" in msg or "unrecognized" in msg:
            kwargs.pop("reasoning_effort", None)
            kwargs.pop("temperature", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    return resp.choices[0].message.content or ""


def _anthropic(system: str, user: str, model: str, max_tokens: int, temperature: float | None) -> str:
    msg = _get_client().messages.create(
        model=model, max_tokens=max_tokens, temperature=0.0 if temperature is None else temperature,
        system=system, messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _raw(system: str, user: str, model: str, max_tokens: int, temperature: float | None) -> str:
    if config.LLM_PROVIDER == "openai":
        return _openai(system, user, model, max_tokens, temperature)
    return _anthropic(system, user, model, max_tokens, temperature)


def complete_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 4000,
                  temperature: float = 0.0, use_cache: bool = True) -> Any:
    """Ask the model for a JSON object/array and parse it."""
    if not config.llm_available():
        raise LLMUnavailable(f"No API key for LLM_PROVIDER={config.LLM_PROVIDER} (see .env.example)")
    model = _resolve_model(model)
    key = cache_key(config.LLM_PROVIDER, model, system, user, max_tokens, temperature)
    if use_cache:
        hit = cache_get("llm", key)
        if hit is not None:
            return hit
    text = _raw(system + _JSON_RULE, user, model, max_tokens, temperature)
    try:
        data = _extract_json(text)
    except json.JSONDecodeError:
        fixed = _raw("Fix the following so it is valid JSON. Output JSON only.", text, _resolve_model("fast"), max_tokens, 0.0)
        data = _extract_json(fixed)
    if use_cache:
        cache_put("llm", key, data)
    return data


def complete_text(system: str, user: str, *, model: str | None = None, max_tokens: int = 1500) -> str:
    if not config.llm_available():
        raise LLMUnavailable(f"No API key for LLM_PROVIDER={config.LLM_PROVIDER} (see .env.example)")
    model = _resolve_model(model)
    key = cache_key("text", config.LLM_PROVIDER, model, system, user, max_tokens)
    hit = cache_get("llm", key)
    if hit is not None:
        return hit
    text = _raw(system, user, model, max_tokens, 0.3)
    cache_put("llm", key, text)
    return text
