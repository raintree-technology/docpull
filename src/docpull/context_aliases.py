"""Bundled source aliases for context dependency workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceType = Literal["auto", "html", "pdf", "markdown", "openapi", "github", "sitemap"]


@dataclass(frozen=True)
class ContextAlias:
    """A named public docs source template."""

    name: str
    title: str
    url: str
    description: str
    homepage: str
    source_type: SourceType = "auto"
    discover: bool = True
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "homepage": self.homepage,
            "type": self.source_type,
            "discover": self.discover,
            "include_paths": list(self.include_paths),
            "exclude_paths": list(self.exclude_paths),
        }


CONTEXT_ALIASES: tuple[ContextAlias, ...] = (
    ContextAlias(
        name="stripe",
        title="Stripe Docs",
        url="https://docs.stripe.com",
        homepage="https://stripe.com/docs",
        description="Stripe payments, billing, API, and SDK documentation.",
    ),
    ContextAlias(
        name="react",
        title="React Docs",
        url="https://react.dev",
        homepage="https://react.dev",
        description="Official React documentation and API reference.",
    ),
    ContextAlias(
        name="nextjs",
        title="Next.js Docs",
        url="https://nextjs.org/docs",
        homepage="https://nextjs.org",
        description="Official Next.js framework documentation.",
    ),
    ContextAlias(
        name="openai",
        title="OpenAI Docs",
        url="https://platform.openai.com/docs",
        homepage="https://platform.openai.com/docs",
        description="OpenAI API guides, references, and examples.",
    ),
    ContextAlias(
        name="postgres",
        title="PostgreSQL Docs",
        url="https://www.postgresql.org/docs/current",
        homepage="https://www.postgresql.org/docs/",
        description="Current PostgreSQL documentation.",
    ),
    ContextAlias(
        name="rust",
        title="The Rust Book",
        url="https://doc.rust-lang.org/book/",
        homepage="https://doc.rust-lang.org/book/",
        description="The official Rust programming language book.",
        source_type="html",
    ),
    ContextAlias(
        name="kubernetes",
        title="Kubernetes Docs",
        url="https://kubernetes.io/docs",
        homepage="https://kubernetes.io/docs/",
        description="Official Kubernetes concepts, tasks, and reference documentation.",
    ),
    ContextAlias(
        name="terraform",
        title="Terraform Docs",
        url="https://developer.hashicorp.com/terraform/docs",
        homepage="https://developer.hashicorp.com/terraform",
        description="Terraform language, CLI, provider, and workflow documentation.",
    ),
    ContextAlias(
        name="aws",
        title="AWS Documentation",
        url="https://docs.aws.amazon.com",
        homepage="https://docs.aws.amazon.com",
        description="AWS service documentation index.",
    ),
    ContextAlias(
        name="apple-hig",
        title="Apple Human Interface Guidelines",
        url="https://developer.apple.com/design/human-interface-guidelines",
        homepage="https://developer.apple.com/design/human-interface-guidelines",
        description="Apple platform design guidance.",
    ),
)

_ALIASES_BY_NAME = {alias.name: alias for alias in CONTEXT_ALIASES}
_ALIASES_BY_URL = {alias.url.rstrip("/"): alias for alias in CONTEXT_ALIASES}


def list_context_aliases() -> list[ContextAlias]:
    """Return bundled aliases in stable display order."""

    return list(CONTEXT_ALIASES)


def get_context_alias(name: str) -> ContextAlias | None:
    """Look up an alias by normalized name."""

    return _ALIASES_BY_NAME.get(name.strip().lower())


def context_alias_for_url(url: str) -> ContextAlias | None:
    """Return the bundled alias that exactly owns a source URL, if any."""

    return _ALIASES_BY_URL.get(url.rstrip("/"))
