"""Keep credentials out of benchmark diagnostics and portable reports."""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_NAME_RE = re.compile(r"(?:API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|token|secret|password|credential|signature|sig)(?:$|[_-])",
    re.IGNORECASE,
)


def scrub_secrets(value: str, *, limit: int = 4000) -> str:
    text = _BEARER_RE.sub("Bearer [REDACTED]", value)
    for name, secret in os.environ.items():
        if _SECRET_NAME_RE.search(name) and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return text.strip()[-limit:]


def sanitize_url(value: str) -> str:
    """Redact secret-shaped query values while preserving a useful public URL."""
    try:
        parts = urlsplit(value)
        query = urlencode(
            [
                (name, "[REDACTED]" if _SENSITIVE_QUERY_RE.search(name) else item)
                for name, item in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except ValueError:
        return "[INVALID_URL]"
