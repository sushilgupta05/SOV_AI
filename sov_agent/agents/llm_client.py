"""Thin provider-agnostic LLM wrapper (OpenAI / Azure OpenAI), JSON mode, temperature 0.

Keys come only from environment variables (never hardcoded).
"""
from __future__ import annotations

import os
from typing import Callable

from sov_agent import config

# A JSON-completion function: (system_prompt, user_prompt) -> raw response text.
JsonCompletion = Callable[[str, str], str]


class LLMUnavailable(Exception):
    """LLM is disabled, not configured, or the call failed."""


def _make_client():
    if not config.LLM_ENABLED:
        raise LLMUnavailable("LLM disabled via LLM_ENABLED=false")
    try:
        if config.LLM_PROVIDER == "azure":
            from openai import AzureOpenAI

            for var in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
                if not os.getenv(var):
                    raise LLMUnavailable(f"{var} is not set")
            return AzureOpenAI(
                api_key=os.environ["AZURE_OPENAI_API_KEY"],
                azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                timeout=config.LLM_TIMEOUT_S,
                max_retries=1,
            )
        from openai import OpenAI

        if not os.getenv("OPENAI_API_KEY"):
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        return OpenAI(timeout=config.LLM_TIMEOUT_S, max_retries=1)
    except ImportError as exc:
        raise LLMUnavailable("openai package not installed") from exc


def default_json_completion() -> JsonCompletion:
    """Return a completion function bound to the configured provider; raises LLMUnavailable."""
    client = _make_client()

    def complete(system: str, user: str) -> str:
        try:
            resp = client.chat.completions.create(
                model=config.LLM_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # network, auth, rate limit, timeout
            raise LLMUnavailable(f"LLM call failed: {type(exc).__name__}") from exc
        return resp.choices[0].message.content or ""

    return complete
