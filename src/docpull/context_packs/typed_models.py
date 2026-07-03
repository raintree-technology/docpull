"""Typed sidecar models for typed context-pack lanes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TypedArtifactModel(BaseModel):
    """Base model for lane sidecars with stable roots and flexible item metadata."""

    model_config = ConfigDict(extra="allow")


class DatasetSchemaArtifact(TypedArtifactModel):
    schema_version: int = 3
    workflow: str
    source_count: int
    item_count: int
    datasets: list[dict[str, Any]] = Field(default_factory=list)


class TranscriptMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    segment_count: int


class PaperMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    workflow: str
    paper_count: int
    papers: list[dict[str, Any]] = Field(default_factory=list)


class RepoMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    source: str
    owner: str
    repo: str
    full_name: str
    html_url: str
    default_branch: str
    resolved_ref: str
    resolved_sha: str
    selected_file_count: int


class PackageMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    ecosystem: str
    name: str
    latest_version: str | None = None
    version_count: int
    registry_url: str


class StandardsMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    workflow: str
    standards: list[dict[str, Any]] = Field(default_factory=list)


class WikiMetadataArtifact(TypedArtifactModel):
    schema_version: int = 3
    workflow: str
    page_count: int
    section_count: int
    pages: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "DatasetSchemaArtifact",
    "PackageMetadataArtifact",
    "PaperMetadataArtifact",
    "RepoMetadataArtifact",
    "StandardsMetadataArtifact",
    "TranscriptMetadataArtifact",
    "TypedArtifactModel",
    "WikiMetadataArtifact",
]
