"""Run identity and artifact schema constants."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .config import DocpullConfig

RUN_IDENTITY_SCHEMA_VERSION = 1
DOCUMENT_RECORD_SCHEMA_VERSION = 1
FRONTIER_SCHEMA_VERSION = 1
MCP_META_SCHEMA_VERSION = 1
PROGRESS_EVENT_SCHEMA_VERSION = 1


class RunIdentity(BaseModel):
    """Stable, non-secret description of the semantics of a docpull run."""

    schema_version: int = RUN_IDENTITY_SCHEMA_VERSION
    profile: str
    start_url: str | None = None
    max_pages: int | None = None
    max_depth: int
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    output_format: str
    naming_strategy: str
    rich_metadata: bool
    extractor: str
    enable_special_cases: bool
    strict_js_required: bool
    max_tokens_per_file: int | None = None
    emit_chunks: bool
    tokenizer: str
    auth_type: str

    @classmethod
    def from_config(cls, config: DocpullConfig) -> RunIdentity:
        return cls(
            profile=config.profile.value,
            start_url=config.url,
            max_pages=config.crawl.max_pages,
            max_depth=config.crawl.max_depth,
            include_paths=sorted(config.crawl.include_paths),
            exclude_paths=sorted(config.crawl.exclude_paths),
            output_format=config.output.format,
            naming_strategy=config.output.naming_strategy,
            rich_metadata=config.output.rich_metadata,
            extractor=config.content_filter.extractor,
            enable_special_cases=config.content_filter.enable_special_cases,
            strict_js_required=config.content_filter.strict_js_required,
            max_tokens_per_file=config.output.max_tokens_per_file,
            emit_chunks=config.output.emit_chunks,
            tokenizer=config.output.tokenizer,
            auth_type=config.auth.type.value,
        )

    def fingerprint(self) -> dict[str, object]:
        """Deterministic compatibility fingerprint for persisted state."""
        return self.model_dump(mode="json", exclude_none=False)
