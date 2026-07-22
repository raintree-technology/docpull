"""Deterministic token estimation for diagnostic report metrics.

Uses tiktoken's cl100k_base encoding when it is importable and its
vocabulary is already cached; otherwise a clearly labeled heuristic
fallback keeps the estimate deterministic and dependency-free. Every
estimate records which estimator produced it so reports stay honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

TIKTOKEN_ENCODING = "cl100k_base"
TIKTOKEN_ESTIMATOR = f"tiktoken:{TIKTOKEN_ENCODING}"
HEURISTIC_ESTIMATOR = "heuristic:max(words,chars/4)"

_WORD_RE = re.compile(r"\S+")
_UNRESOLVED = object()
_encoder: Any = _UNRESOLVED


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    estimator: str


def estimate_tokens(text: str) -> TokenEstimate:
    """Estimate the token count of ``text`` and record the estimator used."""
    encoder = _resolve_encoder()
    if encoder is not None:
        return TokenEstimate(tokens=len(encoder.encode(text)), estimator=TIKTOKEN_ESTIMATOR)
    words = len(_WORD_RE.findall(text))
    return TokenEstimate(tokens=max(words, len(text) // 4), estimator=HEURISTIC_ESTIMATOR)


def _resolve_encoder() -> Any:
    global _encoder
    if _encoder is _UNRESOLVED:
        _encoder = _load_encoder()
    return _encoder


def _load_encoder() -> Any:
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        return tiktoken.get_encoding(TIKTOKEN_ENCODING)
    except Exception:  # tiktoken needs a cached vocabulary; fall back rather than fetch
        return None
