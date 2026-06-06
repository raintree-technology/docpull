"""Reusable MCP prompts for common docpull workflows."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptSpec:
    name: str
    title: str
    description: str
    argument_description: str | None
    template: str


_DOCS_ADD_TEMPLATE = """# Add docs to the local docpull index

User input: {arguments}

The user wants to add documentation to docpull's local index so it is searchable later with grep_docs.

Use this workflow:

1. If the input is empty, reply with:
   Usage: /mcp__docpull__docs_add <alias>, /mcp__docpull__docs_add <https-url>,
   or /mcp__docpull__docs_add <name> <https-url>.
2. If the input is one token without a URL scheme, treat it as a source alias
   and call ensure_docs(source=<alias>).
3. If the input is one HTTPS URL, derive an alias from the hostname by stripping
   a leading docs. or www., taking the first label, and lowercasing it. Check
   list_sources and list_indexed for collisions. If there is no collision, call
   add_source(name=<derived>, url=<url>) and then ensure_docs(source=<derived>).
4. If the input is two tokens and the second token is an HTTPS URL, validate the
   first token as an alias, call add_source(name=<name>, url=<url>), then
   ensure_docs(source=<name>).

After success, report the alias and the fetch counts from ensure_docs. If a URL
is rejected, explain that docpull only accepts public HTTPS docs URLs.
"""

_DOCS_SEARCH_TEMPLATE = """# Search fetched docs

User input: {arguments}

The user wants to search documentation that has already been fetched into docpull's local index.

Use this workflow:

1. If the input is empty, reply with: Usage: /mcp__docpull__docs_search <pattern> [library].
2. Parse the first argument as the regex pattern and the optional second argument as the library alias.
3. Call grep_docs(pattern=<pattern>, library=<library if provided>, limit=10, context=2).
4. For the top two or three useful file hits, call read_doc with the returned
   library and path, using a narrow line window around the first match.
5. If no library was provided and nothing matches, broaden the regex once. If a
   library was provided and nothing matches, call list_indexed to confirm the
   library exists.

Answer with the synthesized result, citing docpull paths as library/path.md:line.
Do not dump raw grep output unless the user asks for it.
"""

_DOCS_LIST_TEMPLATE = """# List cached docs

Show what documentation libraries are available in the local docpull index.

Use this workflow:

1. Call list_indexed().
2. If nothing is cached, say that no docs are indexed yet and suggest /mcp__docpull__docs_add <alias-or-url>.
3. If libraries are cached, render the list concisely with file counts and freshness.
4. If any cached library is stale, suggest /mcp__docpull__docs_refresh <library>.
"""

_DOCS_REFRESH_TEMPLATE = """# Refresh cached docs

User input: {arguments}

The user wants to force-refresh a fetched documentation library, bypassing the normal cache.

Use this workflow:

1. If the input is empty, reply with: Usage: /mcp__docpull__docs_refresh <library>.
2. Parse the input as a single library alias.
3. Call ensure_docs(source=<library>, force=true).
4. After success, summarize pages fetched, skipped, and failed from the tool response.

Do not refresh every cached library unless the user explicitly asks for that broader operation.
"""

_DOCS_REMOVE_TEMPLATE = """# Remove a docs source

User input: {arguments}

The user wants to remove a user-defined source alias and, by default, delete its cached docs.

Use this workflow:

1. If the input is empty, reply with: Usage: /mcp__docpull__docs_remove <library> [--keep-cache].
2. Parse the first token as the library alias.
3. If --keep-cache is present, call remove_source(name=<library>, delete_cache=false).
4. Otherwise call remove_source(name=<library>, delete_cache=true).
5. Relay the tool result plainly. If the source is builtin, explain that builtins cannot be removed.

Do not delete files with shell commands; use remove_source for validated cache deletion.
"""

PROMPTS: tuple[PromptSpec, ...] = (
    PromptSpec(
        name="docs_add",
        title="Add docs",
        description="Fetch a built-in docs alias or register an HTTPS docs URL and index it locally.",
        argument_description="<alias> | <https-url> | <name> <https-url>",
        template=_DOCS_ADD_TEMPLATE,
    ),
    PromptSpec(
        name="docs_search",
        title="Search docs",
        description="Search fetched docs by regex and read surrounding context from the best hits.",
        argument_description="<pattern> [library]",
        template=_DOCS_SEARCH_TEMPLATE,
    ),
    PromptSpec(
        name="docs_list",
        title="List cached docs",
        description="List documentation libraries currently cached locally.",
        argument_description=None,
        template=_DOCS_LIST_TEMPLATE,
    ),
    PromptSpec(
        name="docs_refresh",
        title="Refresh docs",
        description="Force-refresh a cached documentation library.",
        argument_description="<library>",
        template=_DOCS_REFRESH_TEMPLATE,
    ),
    PromptSpec(
        name="docs_remove",
        title="Remove docs",
        description="Remove a user-defined source alias, optionally keeping cached docs.",
        argument_description="<library> [--keep-cache]",
        template=_DOCS_REMOVE_TEMPLATE,
    ),
)

_PROMPTS_BY_NAME = {prompt.name: prompt for prompt in PROMPTS}


def render_prompt(name: str, arguments: dict[str, str] | None = None) -> str:
    """Render a prompt by name for the MCP server."""
    try:
        prompt = _PROMPTS_BY_NAME[name]
    except KeyError:
        raise ValueError(f"Unknown prompt: {name}") from None

    raw_arguments = ""
    if arguments:
        raw_arguments = arguments.get("input", "")

    return prompt.template.format(arguments=raw_arguments or "(empty)")


__all__ = ["PROMPTS", "PromptSpec", "render_prompt"]
