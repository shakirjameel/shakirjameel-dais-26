"""
llm_client.py — provider-agnostic LLM client. THE swap point.

One OpenAI-compatible client serves Databricks-served Claude, OpenAI, and Anthropic. The only
thing that differs is how the client is constructed:
  - Databricks mode (no key): the Databricks SDK mints/refreshes the bearer token from the CLI
    profile and returns an OpenAI client pointed at the workspace serving endpoint.
  - Explicit key: a plain OpenAI client against LLM_BASE_URL with LLM_API_KEY.

Everything downstream (orchestrator, tools) talks to `chat()` and never knows the provider.
Tool-calling uses the standard OpenAI `tools` / `tool_calls` schema (works on Databricks FM
serving for Claude, OpenAI, and Anthropic's OpenAI-compatible endpoint alike).
"""

from __future__ import annotations

from functools import lru_cache

from . import config


def _use_os_trust_store() -> None:
    """Route SSL through the OS trust store (no-op if `truststore` absent). Needed behind a
    TLS-intercepting proxy (e.g. Zscaler) whose root CA the bundled certifi store doesn't have —
    openai/httpx use certifi by default, so without this the call hangs/fails on the handshake."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass


@lru_cache(maxsize=1)
def _client():
    _use_os_trust_store()
    if config.IS_DATABRICKS:
        # Databricks SDK handles OAuth (no static token in .env); returns an OpenAI client
        # whose base_url is already the workspace /serving-endpoints.
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient(profile=config.DATABRICKS_PROFILE).serving_endpoints.get_open_ai_client()
    from openai import OpenAI
    return OpenAI(base_url=config.BASE_URL, api_key=config.API_KEY)


def chat(messages: list[dict], tools: list[dict] | None = None,
         tool_choice: str = "auto", max_tokens: int = 4096, temperature: float | None = None):
    """
    One chat-completions turn. Returns the OpenAI-style response; the caller inspects
    `.choices[0].message` for content and `.tool_calls`. No provider-specific branching here.

    `temperature` is omitted by default — newer Claude models (Opus 4.7/4.8) 400 on it, while
    OpenAI just uses its own default. Pass a value only for providers/models that accept it.
    """
    kwargs = {"model": config.MODEL, "messages": messages, "max_tokens": max_tokens,
              "timeout": 60}  # fail fast rather than hang on a blocked connection
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    return _client().chat.completions.create(**kwargs)


def describe() -> dict:
    """What the client is wired to (for the smoke test / diagnostics). No secrets."""
    return {
        "mode": "databricks (CLI auth)" if config.IS_DATABRICKS else "explicit key",
        "base_url": config.BASE_URL or "(databricks default)",
        "model": config.MODEL,
        "has_explicit_key": bool(config.API_KEY),
    }


if __name__ == "__main__":
    import json
    print("client:", json.dumps(describe(), indent=2))
    r = chat([{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=16)
    print("response:", r.choices[0].message.content)
