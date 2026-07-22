"""AI/TDM opt-out signal parsing and policy decisions.

Publishers can attach machine-readable opt-out signals to individual pages
without blocking ordinary crawling in robots.txt. docpull honors two
carriers of those signals:

- the ``X-Robots-Tag`` HTTP response header, and
- ``<meta name="robots" content="...">`` tags in HTML (including
  ``docpull``-scoped variants such as ``<meta name="docpull" ...>``).

The directives ``noai`` and ``noimageai`` are explicit AI / text-and-data-
mining reuse opt-outs and are honored by default. ``noindex`` and ``none``
are search-indexing controls, not reuse opt-outs, so they only block under
a stricter optional mode (see :func:`evaluate_optout`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The user-agent token that scoped directives must name to apply to us.
USER_AGENT_TOKEN = "docpull"

# Directives that signal an AI/TDM reuse opt-out.
AI_OPTOUT_DIRECTIVES = frozenset({"noai", "noimageai"})

# Directives that signal a search-indexing opt-out (``none`` implies
# ``noindex, nofollow``).
NOINDEX_DIRECTIVES = frozenset({"noindex", "none"})

# X-Robots-Tag directives that carry a value after a colon. These must not
# be mistaken for ``useragent:`` scope prefixes when parsing.
_VALUED_DIRECTIVES = frozenset(
    {
        "max-snippet",
        "max-image-preview",
        "max-video-preview",
        "unavailable_after",
    }
)

OptOutSource = Literal["x-robots-tag", "meta-robots"]


@dataclass(frozen=True)
class OptOutDecision:
    """Outcome of evaluating opt-out directives for one page."""

    blocked: bool
    matched: tuple[str, ...]
    source: OptOutSource


def parse_x_robots_tag(header_value: str) -> set[str]:
    """Parse one ``X-Robots-Tag`` header value into applicable directives.

    Directives are comma-separated (space-separated also accepted). A value
    may open with an optional ``useragent:`` prefix that scopes every
    following directive to that agent, e.g. ``googlebot: noindex, nofollow``.
    Directives scoped to ``docpull`` and unscoped (global) directives apply;
    directives scoped to any other agent are ignored. Matching is
    case-insensitive. Valued directives such as ``max-snippet:0`` keep only
    their name.

    Args:
        header_value: Raw header value as received from the server.

    Returns:
        Lowercase directive names that apply to docpull.
    """
    directives: set[str] = set()
    scope: str | None = None  # None = applies to every agent
    for raw_token in header_value.split(","):
        token = raw_token.strip()
        if not token:
            continue
        prefix, sep, rest = token.partition(":")
        if sep and prefix.strip().casefold() not in _VALUED_DIRECTIVES:
            scope = prefix.strip().casefold()
            token = rest.strip()
        if scope is not None and scope != USER_AGENT_TOKEN:
            continue
        name = token.partition(":")[0]
        for word in name.split():
            directives.add(word.casefold())
    return directives


def parse_robots_meta(content_attr: str) -> set[str]:
    """Parse a robots ``<meta>`` tag ``content`` attribute into directives.

    Accepts comma- and/or space-separated directives; valued directives such
    as ``max-snippet:0`` keep only their name. Matching is case-insensitive.

    Args:
        content_attr: The raw ``content="..."`` attribute value.

    Returns:
        Lowercase directive names.
    """
    directives: set[str] = set()
    for raw_token in content_attr.replace(",", " ").split():
        name = raw_token.partition(":")[0].strip().casefold()
        if name:
            directives.add(name)
    return directives


def evaluate_optout(
    directives: set[str],
    *,
    respect_noai: bool,
    respect_noindex: bool = False,
    source: OptOutSource,
) -> OptOutDecision:
    """Decide whether a page's directives opt it out of collection.

    ``noai`` / ``noimageai`` are explicit AI/TDM reuse opt-outs and block
    whenever ``respect_noai`` is set (the product default is True).

    ``noindex`` / ``none`` are deliberately separated: they tell search
    engines not to list a page in results, which is a statement about
    discoverability, not about whether the content may be read or reused.
    A publisher may well noindex a page (say, a changelog or a printer
    view) while being fine with it being mirrored. They therefore block
    only under the stricter ``respect_noindex`` flag (default False).

    Args:
        directives: Applicable lowercase directives for this page.
        respect_noai: Honor ``noai`` / ``noimageai`` opt-outs.
        respect_noindex: Also honor ``noindex`` / ``none`` (strict mode).
        source: Which carrier the directives came from.

    Returns:
        An :class:`OptOutDecision` with the matched blocking directives.
    """
    matched: set[str] = set()
    if respect_noai:
        matched |= AI_OPTOUT_DIRECTIVES & directives
    if respect_noindex:
        matched |= NOINDEX_DIRECTIVES & directives
    return OptOutDecision(
        blocked=bool(matched),
        matched=tuple(sorted(matched)),
        source=source,
    )
