"""Shared HTML fetch helper for link-discovery components."""

from __future__ import annotations

import logging

from ..http.protocols import HttpClient

logger = logging.getLogger(__name__)


async def fetch_html(client: HttpClient, url: str) -> bytes | None:
    """Fetch ``url`` and return its body iff it is a successful HTML response.

    Returns ``None`` on network error, non-200 status, or a non-HTML content
    type. Shared by the crawler and the static/enhanced link extractors so the
    fetch and content-type gate stay identical across all three.
    """
    try:
        response = await client.get(url, timeout=30.0)

        if response.status_code != 200:
            return None

        content_type = response.content_type.lower()
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None

        return response.content

    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return None
