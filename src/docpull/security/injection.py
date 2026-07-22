"""Deterministic prompt-injection screening for captured content.

Web pages captured into a corpus are a prime indirect-prompt-injection
vector once they are fed to AI agents. :func:`screen_text` runs a local,
deterministic pattern pass over converted Markdown and reports
instruction-like payloads grouped into named families:

- ``direct_override``: imperatives aimed at AI systems ("ignore previous
  instructions", "you are now", "act as", "new instructions", "system
  prompt", "developer message").
- ``exfiltration``: exfiltration/tool-abuse cues ("send ... to http",
  "curl ... | sh", "run the following command").
- ``credential_fishing``: requests for secrets ("enter your API key",
  "paste your token").
- ``agent_markers``: chat-template markers targeted at agents
  ("<|im_start|>", "[INST]", "### Instruction", "BEGIN SYSTEM PROMPT").
- ``obfuscation``: invisible-text vectors (long zero-width character runs,
  RTL-override characters).

IMPORTANT calibration: the resulting trust label is ADVISORY METADATA, not
a block. These patterns intentionally over-trigger — legitimate
documentation *about* prompt injection, or ordinary prose containing "act
as", will be labelled ``suspicious``. Callers must never skip or fail a
page based on this label; it exists so downstream agents can decide how
much to trust the content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SCREEN_VERSION = 1

# Screening is bounded: only the first MAX_SCAN_CHARS characters are
# scanned so pathological pages cannot stall the pipeline.
MAX_SCAN_CHARS = 2_000_000
EXCERPT_MAX_CHARS = 120

TrustLabel = Literal["clean", "suspicious"]


@dataclass(frozen=True)
class InjectionSpan:
    """One flagged region of the screened text."""

    pattern_id: str
    family: str
    start: int
    end: int
    excerpt: str

    def as_dict(self) -> dict[str, object]:
        return {
            "pattern_id": self.pattern_id,
            "family": self.family,
            "start": self.start,
            "end": self.end,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class InjectionScreenResult:
    """Outcome of one screening pass. Advisory only — never a block signal."""

    spans: tuple[InjectionSpan, ...] = ()
    truncated: bool = False
    screen_version: int = SCREEN_VERSION
    families: tuple[str, ...] = field(init=False)
    trust_label: TrustLabel = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "families", tuple(sorted({span.family for span in self.spans})))
        object.__setattr__(self, "trust_label", "suspicious" if self.spans else "clean")

    @property
    def suspicious(self) -> bool:
        return self.trust_label == "suspicious"

    def summary(self) -> dict[str, object]:
        """Compact dict for manifests: label, families, counts — no spans."""
        payload: dict[str, object] = {
            "trust_label": self.trust_label,
            "families": list(self.families),
            "span_count": len(self.spans),
            "screen_version": self.screen_version,
        }
        if self.truncated:
            payload["truncated"] = True
        return payload

    def span_dicts(self) -> list[dict[str, object]]:
        return [span.as_dict() for span in self.spans]


@dataclass(frozen=True)
class _ScreenPattern:
    pattern_id: str
    family: str
    regex: re.Pattern[str]


def _p(pattern_id: str, family: str, pattern: str, flags: int = 0) -> _ScreenPattern:
    return _ScreenPattern(pattern_id=pattern_id, family=family, regex=re.compile(pattern, flags))


# Simple alternations with bounded gaps only — no nested quantifiers, so no
# catastrophic backtracking. Compiled once at import.
_PATTERNS: tuple[_ScreenPattern, ...] = (
    _p(
        "ignore_previous_instructions",
        "direct_override",
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?"
        r"(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|directions?|prompts?|messages?|rules?)\b",
        re.IGNORECASE,
    ),
    _p(
        # "you are now ready/able/..." is everyday tutorial prose; the
        # lookahead keeps the pattern aimed at persona reassignment.
        "you_are_now",
        "direct_override",
        r"\byou\s+are\s+now\s+"
        r"(?!ready\b|able\b|all\s+set\b|set\b|done\b|connected\b|logged\b|signed\b|"
        r"subscribed\b|registered\b|running\b)\w",
        re.IGNORECASE,
    ),
    _p("act_as", "direct_override", r"\bact\s+as\b", re.IGNORECASE),
    _p("new_instructions", "direct_override", r"\bnew\s+instructions\b", re.IGNORECASE),
    _p("system_prompt", "direct_override", r"\bsystem\s+prompt\b", re.IGNORECASE),
    _p("developer_message", "direct_override", r"\bdeveloper\s+message\b", re.IGNORECASE),
    _p(
        "exfil_to_url",
        "exfiltration",
        r"\b(?:send|post|exfiltrate|forward|upload|transmit)\b[^\n]{0,80}?https?://",
        re.IGNORECASE,
    ),
    _p(
        "curl_pipe_shell",
        "exfiltration",
        r"\b(?:curl|wget)\b[^\n|]{0,200}\|\s*(?:sudo\s+)?(?:ba|z)?sh\b",
        re.IGNORECASE,
    ),
    _p("run_following_command", "exfiltration", r"\brun\s+the\s+following\s+command\b", re.IGNORECASE),
    _p(
        "enter_credentials",
        "credential_fishing",
        r"\benter\s+your\s+(?:api[\s_-]?key|access\s+token|token|secret|password|credentials)\b",
        re.IGNORECASE,
    ),
    _p(
        "paste_credentials",
        "credential_fishing",
        r"\bpaste\s+your\s+(?:api[\s_-]?key|access\s+token|token|secret|password|credentials)\b",
        re.IGNORECASE,
    ),
    _p("chatml_marker", "agent_markers", r"<\|im_(?:start|end)\|>"),
    _p("inst_marker", "agent_markers", r"\[/?INST\]"),
    _p(
        "instruction_heading",
        "agent_markers",
        r"^\s{0,3}#{1,6}\s*Instruction\b",
        re.IGNORECASE | re.MULTILINE,
    ),
    _p("begin_system_prompt", "agent_markers", r"\bBEGIN\s+SYSTEM\s+PROMPT\b", re.IGNORECASE),
    _p("zero_width_run", "obfuscation", "[\u200b\u200c\u200d\u2060\ufeff]{3,}"),
    _p("rtl_override", "obfuscation", "[\u202a-\u202e]"),
)


def _sanitize_excerpt(raw: str) -> str:
    """Make an excerpt safe to print: printable chars only, collapsed whitespace."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:EXCERPT_MAX_CHARS]


def screen_text(text: str) -> InjectionScreenResult:
    """Screen captured text for instruction-like payloads.

    Deterministic and local: a fixed set of compiled regexes, scanned over at
    most :data:`MAX_SCAN_CHARS` characters (``truncated`` is set when input
    exceeds the cap).

    The result is advisory metadata. A ``suspicious`` label means the text
    contains phrasing that *could* steer an AI agent — including legitimate
    documentation about prompt injection. Never skip or fail a page because
    of it.
    """
    truncated = len(text) > MAX_SCAN_CHARS
    scanned = text[:MAX_SCAN_CHARS] if truncated else text

    spans: list[InjectionSpan] = []
    for pattern in _PATTERNS:
        for match in pattern.regex.finditer(scanned):
            spans.append(
                InjectionSpan(
                    pattern_id=pattern.pattern_id,
                    family=pattern.family,
                    start=match.start(),
                    end=match.end(),
                    excerpt=_sanitize_excerpt(match.group(0)),
                )
            )
    spans.sort(key=lambda span: (span.start, span.end, span.pattern_id))
    return InjectionScreenResult(spans=tuple(spans), truncated=truncated)
