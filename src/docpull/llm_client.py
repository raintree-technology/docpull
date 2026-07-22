"""Dependency-free BYOK client for the Anthropic Messages API.

docpull never calls a hosted model by default. This client exists only for
explicit opt-in, bring-your-own-key (BYOK) features such as
``docpull extract --mode llm``. Transport conventions mirror
``docpull.judge``: plain ``urllib`` with no SDK dependency, key-gated on
``ANTHROPIC_API_KEY``, and easy to replace with a fake client in tests.
The API key is never logged, echoed, or included in ``repr`` output.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping

ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
LLM_MODEL_ENV = "DOCPULL_LLM_MODEL"
DEFAULT_LLM_MODEL = "claude-opus-4-8"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
LLM_REQUEST_TIMEOUT_S = 120.0
DEFAULT_MAX_TOKENS = 2000


class LlmTransportError(RuntimeError):
    """Raised when a BYOK model call fails; messages never include the key."""


class AnthropicMessagesClient:
    """Minimal urllib-based Anthropic Messages client (BYOK, no SDK)."""

    def __init__(self, api_key: str, model: str, base_url: str = ANTHROPIC_MESSAGES_URL) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url

    def __repr__(self) -> str:
        return f"AnthropicMessagesClient(model={self.model!r})"

    def complete(self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """Send one system+user prompt and return the first text block."""
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode()
        request = urllib.request.Request(
            self._base_url,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=LLM_REQUEST_TIMEOUT_S) as response:  # nosec B310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise LlmTransportError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise LlmTransportError(str(exc.reason)) from exc
        except json.JSONDecodeError as exc:
            raise LlmTransportError("response was not JSON") from exc
        content = payload.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        return text
        raise LlmTransportError("response missing a text content block")


def resolve_model(model: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Return the explicit model, the ``DOCPULL_LLM_MODEL`` override, or the default."""
    environment: Mapping[str, str] = os.environ if env is None else env
    return model or environment.get(LLM_MODEL_ENV) or DEFAULT_LLM_MODEL


def resolve_client(
    model: str | None = None,
    env: Mapping[str, str] | None = None,
) -> AnthropicMessagesClient | None:
    """Return a BYOK client when ``ANTHROPIC_API_KEY`` is set; otherwise ``None``."""
    environment: Mapping[str, str] = os.environ if env is None else env
    api_key = environment.get(ANTHROPIC_API_KEY_ENV)
    if not api_key:
        return None
    return AnthropicMessagesClient(api_key=api_key, model=resolve_model(model, environment))
