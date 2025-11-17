"""Language filter processor - removes files in unwanted languages."""

import re
from pathlib import Path
from typing import Optional, Union

from .base import BaseProcessor, ProcessorContext, ProcessorResult


class LanguageFilter(BaseProcessor):
    """Filter files by language code.

    Detects language from URL patterns and file paths, removing files
    that don't match the specified language(s).

    Configuration:
        include: List of language codes to keep (e.g., ['en', 'es'])
        exclude: List of language codes to remove (e.g., ['de', 'fr'])
        patterns: Custom regex patterns to detect language (optional)
        default_keep: If language cannot be detected, keep file (default: True)

    Example:
        # Keep only English
        LanguageFilter({'include': ['en']})

        # Remove specific languages
        LanguageFilter({'exclude': ['de', 'es', 'fr', 'ja', 'ko', 'zh', 'pt', 'id', 'it']})
    """

    # Common language detection patterns
    DEFAULT_PATTERNS = [
        r"/(?P<lang>[a-z]{2})/",  # /en/, /de/, etc.
        r"[_-](?P<lang>[a-z]{2})[_-]",  # docs_en_, _en_, -en-
        r"docs_(?P<lang>[a-z]{2})_",  # docs_en_
        r"/(?P<lang>[a-z]{2})-[A-Z]{2}/",  # /en-US/
        r"\.(?P<lang>[a-z]{2})\.",  # .en., .de.
    ]

    def __init__(self, config: Optional[dict[str, Union[str, int, bool, list[str], None]]] = None):
        """Initialize language filter.

        Args:
            config: Configuration dict with 'include', 'exclude', 'patterns', 'default_keep'
        """
        super().__init__(config)
        self.include_langs: set[str] = set(self.config.get("include", []))  # type: ignore[arg-type]
        self.exclude_langs: set[str] = set(self.config.get("exclude", []))  # type: ignore[arg-type]
        self.default_keep: bool = self.config.get("default_keep", True)  # type: ignore[assignment]

        # Compile patterns
        pattern_strings = self.config.get("patterns", self.DEFAULT_PATTERNS)
        self.patterns = [re.compile(p, re.IGNORECASE) for p in pattern_strings]  # type: ignore[union-attr]

        # Validation
        if self.include_langs and self.exclude_langs:
            overlap = self.include_langs & self.exclude_langs
            if overlap:
                raise ValueError(f"Language codes appear in both include and exclude: {overlap}")

    def _detect_language(self, url_or_path: str) -> Optional[str]:
        """Detect language code from a URL or path string.

        Args:
            url_or_path: URL or file path string

        Returns:
            Language code (e.g., 'en', 'de') or None if not detected
        """
        for pattern in self.patterns:
            match = pattern.search(url_or_path)
            if match:
                return match.group("lang").lower()
        return None

    def detect_language(self, file_path: Path, metadata: dict[str, Union[str, int, None]]) -> Optional[str]:
        """Detect language code from file path and metadata.

        Args:
            file_path: Path to file
            metadata: File metadata (may contain 'url')

        Returns:
            Language code (e.g., 'en', 'de') or None if not detected
        """
        # Check URL in metadata first (more reliable than file path)
        if "url" in metadata:
            lang = self._detect_language(str(metadata["url"]))
            if lang:
                return lang

        # Fallback to file path
        lang = self._detect_language(str(file_path))
        if lang:
            return lang

        return None

    def should_keep_file(self, file_path: Path, metadata: dict[str, Union[str, int, None]]) -> bool:
        """Determine if file should be kept based on language.

        Args:
            file_path: Path to file
            metadata: File metadata

        Returns:
            True if file should be kept
        """
        lang = self.detect_language(file_path, metadata)

        if lang is None:
            # Language not detected
            return self.default_keep

        # Include list takes precedence
        if self.include_langs:
            return lang in self.include_langs

        # Exclude list
        if self.exclude_langs:
            return lang not in self.exclude_langs

        # No filters configured, keep all
        return True

    def process(self, context: ProcessorContext) -> ProcessorResult:
        """Filter files by language.

        Args:
            context: ProcessorContext with files to filter

        Returns:
            ProcessorResult with filtered files
        """
        if not self.include_langs and not self.exclude_langs:
            # No filtering needed
            return ProcessorResult(
                files=context.files,
                metadata=context.metadata,
                stats={"filtered": 0, "kept": len(context.files)},
            )

        kept_files: list[Path] = []
        removed_files: list[Path] = []
        lang_stats: dict[str, int] = {}

        for file_path in context.files:
            metadata = context.metadata.get(file_path, {})

            if self.should_keep_file(file_path, metadata):
                kept_files.append(file_path)
                lang = self.detect_language(file_path, metadata) or "unknown"
                lang_stats[lang] = lang_stats.get(lang, 0) + 1
            else:
                removed_files.append(file_path)
                self.logger.debug(f"Filtered out: {file_path}")

        # Calculate size saved
        size_saved = sum(context.metadata.get(f, {}).get("size", 0) for f in removed_files)  # type: ignore[misc]

        messages = [
            f"Language filter: kept {len(kept_files)}/{len(context.files)} files",
            f"Removed {len(removed_files)} files ({size_saved / 1024 / 1024:.1f} MB)",
        ]

        if self.include_langs:
            messages.append(f"Included languages: {', '.join(sorted(self.include_langs))}")
        if self.exclude_langs:
            messages.append(f"Excluded languages: {', '.join(sorted(self.exclude_langs))}")

        return ProcessorResult(
            files=kept_files,
            metadata={f: context.metadata[f] for f in kept_files},
            removed_files=removed_files,
            stats={
                "kept": len(kept_files),
                "filtered": len(removed_files),
                "size_saved_bytes": size_saved,
                "languages": lang_stats,  # type: ignore[dict-item]
            },
            messages=messages,
        )
