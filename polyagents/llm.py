"""Provider-aware chat-LLM factory — one place that decides *which* LLM backend.

Every LLM construction in the app goes through :func:`build_chat_llm` so a single
env switch flips the whole system between Anthropic (default) and any OpenAI-
compatible endpoint (DeepSeek, a LiteLLM gateway, local vLLM, …). Keeps the call
sites identical; only the wiring here knows about providers.

Selection (first match wins):
  * ``LLM_PROVIDER=deepseek`` (or just ``DEEPSEEK_API_KEY`` set, no provider) →
    ``ChatOpenAI`` pointed at ``DEEPSEEK_BASE_URL`` (default https://api.deepseek.com)
    with model ``DEEPSEEK_MODEL`` (default ``deepseek-chat``).
  * ``LLM_PROVIDER=openai`` with ``OPENAI_BASE_URL`` → generic OpenAI-compatible.
  * otherwise → ``ChatAnthropic`` (the historical default). Set
    ``ANTHROPIC_BASE_URL`` to route Anthropic calls through a compatible gateway.

``model`` is the resolved Anthropic model id from the call site; it is used for
the Anthropic path and ignored for OpenAI-compatible providers (which pin their
own model via ``*_MODEL``).
"""
from __future__ import annotations

import os


def _openai_compatible(model: str | None, temperature: float, *, api_key: str,
                       base_url: str, default_model: str):
    from langchain_openai import ChatOpenAI

    # Honor a caller-supplied model only if it's a real model for THIS provider
    # (the UI can pass 'deepseek-chat' / 'deepseek-reasoner'); Anthropic ids from
    # internal call sites are ignored and fall back to the env / default.
    prefix = default_model.split("-", 1)[0]                # 'deepseek' / 'gpt' / …
    passthru = model if (model and str(model).startswith(prefix)) else None
    return ChatOpenAI(
        model=passthru or os.getenv("DEEPSEEK_MODEL") or os.getenv("OPENAI_MODEL") or default_model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )


def structured_output(llm, schema):
    """``llm.with_structured_output(schema)`` that works across providers.

    Forces ``method="function_calling"`` — Anthropic does structured output via
    tools anyway, and OpenAI-compatible providers like DeepSeek support function
    calling but NOT the ``response_format=json_schema`` mode langchain_openai
    defaults to (which 400s with 'unavailable response format'). Falls back to the
    plain call for any llm that doesn't accept the ``method`` kwarg (e.g. fakes)."""
    try:
        return llm.with_structured_output(schema, method="function_calling")
    except TypeError:
        return llm.with_structured_output(schema)


def build_chat_llm(model: str | None = None, temperature: float = 0.0):
    """Return a chat model for the configured provider (see module docstring)."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    ds_key = os.getenv("DEEPSEEK_API_KEY")

    if provider == "deepseek" or (not provider and ds_key):
        if not ds_key:
            raise RuntimeError("LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set")
        return _openai_compatible(
            model, temperature, api_key=ds_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            default_model="deepseek-chat")

    if provider == "openai":
        return _openai_compatible(
            model, temperature, api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            default_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    from langchain_anthropic import ChatAnthropic
    from polyagents.default_config import DEFAULT_CONFIG

    return ChatAnthropic(
        model=model or DEFAULT_CONFIG["anthropic_model"],
        temperature=temperature,
        base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
    )
