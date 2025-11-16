"""Deduplication processor - removes duplicate files based on content hash."""

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Optional

from .base import BaseProcessor, ProcessorContext, ProcessorResult


class Deduplicator(BaseProcessor):
    """Remove duplicate files based on content hash.

    Identifies duplicate files by computing SHA-256 hashes and keeps
    only one copy based on configurable criteria.

    Configuration:
        enabled: Whether deduplication is enabled (default: True)
        keep_variant: Pattern to prefer when keeping duplicates (e.g., 'mainnet')
        keep_strategy: Strategy for choosing which duplicate to keep
                      ('first', 'last', 'shortest', 'longest', 'pattern')
        remove_patterns: List of patterns to always remove if duplicate (e.g., ['*_testnet_*'])
        hash_algorithm: Hash algorithm to use (default: 'sha256')

    Example:
        # Keep mainnet version of Aptos docs
        Deduplicator({'keep_variant': 'mainnet'})

        # Remove testnet/devnet variants
        Deduplicator({'remove_patterns': ['*_testnet_*', '*_devnet_*']})
    """

    def __init__(self, config: Optional[dict[str, any]] = None):
        """Initialize deduplicator.

        Args:
            config: Configuration dict
        """
        super().__init__(config)
        self.enabled: bool = self.config.get("enabled", True)
        self.keep_variant: Optional[str] = self.config.get("keep_variant")
        self.keep_strategy: str = self.config.get(
            "keep_strategy", "pattern" if self.keep_variant else "first"
        )
        self.remove_patterns: list[str] = self.config.get("remove_patterns", [])
        self.hash_algorithm: str = self.config.get("hash_algorithm", "sha256")

    def compute_hash(self, file_path: Path) -> str:
        """Compute hash of file content.

        Args:
            file_path: Path to file

        Returns:
            Hex digest of file hash
        """
        hasher = hashlib.new(self.hash_algorithm)

        try:
            with open(file_path, "rb") as f:
                # Read in chunks to handle large files
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as e:
            self.logger.warning(f"Failed to hash {file_path}: {e}")
            # Return path-based hash as fallback
            return hashlib.sha256(str(file_path).encode()).hexdigest()

    def matches_pattern(self, file_path: Path, pattern: str) -> bool:
        """Check if file path matches a pattern.

        Args:
            file_path: Path to check
            pattern: Glob-style pattern (*, ?, etc.)

        Returns:
            True if path matches pattern
        """
        from fnmatch import fnmatch

        return fnmatch(str(file_path), pattern)

    def should_remove(self, file_path: Path) -> bool:
        """Check if file matches remove patterns.

        Args:
            file_path: Path to check

        Returns:
            True if file should be removed
        """
        return any(self.matches_pattern(file_path, pattern) for pattern in self.remove_patterns)

    def choose_file_to_keep(self, duplicates: list[Path]) -> Path:
        """Choose which duplicate file to keep.

        Args:
            duplicates: List of duplicate file paths

        Returns:
            Path of file to keep
        """
        if len(duplicates) == 1:
            return duplicates[0]

        # Strategy: pattern (keep_variant)
        if self.keep_strategy == "pattern" and self.keep_variant:
            for file_path in duplicates:
                if self.keep_variant in str(file_path):
                    return file_path

        # Strategy: shortest path
        if self.keep_strategy == "shortest":
            return min(duplicates, key=lambda p: len(str(p)))

        # Strategy: longest path
        if self.keep_strategy == "longest":
            return max(duplicates, key=lambda p: len(str(p)))

        # Strategy: last (reverse order)
        if self.keep_strategy == "last":
            return duplicates[-1]

        # Strategy: first (default)
        return duplicates[0]

    def process(self, context: ProcessorContext) -> ProcessorResult:
        """Remove duplicate files.

        Args:
            context: ProcessorContext with files to deduplicate

        Returns:
            ProcessorResult with deduplicated files
        """
        if not self.enabled:
            return ProcessorResult(files=context.files, metadata=context.metadata, stats={"enabled": False})

        # Group files by hash
        hash_to_files: dict[str, list[Path]] = defaultdict(list)
        file_to_hash: dict[Path, str] = {}

        self.logger.info(f"Computing hashes for {len(context.files)} files")

        for file_path in context.files:
            if self.should_skip(file_path, context):
                continue

            file_hash = self.compute_hash(file_path)
            hash_to_files[file_hash].append(file_path)
            file_to_hash[file_path] = file_hash

        # Find duplicates
        duplicates_found = {h: files for h, files in hash_to_files.items() if len(files) > 1}

        if not duplicates_found:
            return ProcessorResult(
                files=context.files, metadata=context.metadata, stats={"duplicates": 0, "removed": 0}
            )

        # Process duplicates
        kept_files: list[Path] = []
        removed_files: list[Path] = []

        for _, duplicate_group in hash_to_files.items():
            if len(duplicate_group) == 1:
                # Not a duplicate
                kept_files.append(duplicate_group[0])
                continue

            # Filter by remove patterns first
            candidates = [f for f in duplicate_group if not self.should_remove(f)]

            if not candidates:
                # All matched remove patterns, keep first original
                candidates = [duplicate_group[0]]
                self.logger.warning(f"All duplicates matched remove patterns, keeping {candidates[0]}")

            # Choose which to keep
            to_keep = self.choose_file_to_keep(candidates)
            kept_files.append(to_keep)

            # Remove others
            for file_path in duplicate_group:
                if file_path != to_keep:
                    removed_files.append(file_path)
                    self.logger.debug(f"Removing duplicate: {file_path} (keeping {to_keep})")

        # Calculate stats
        size_saved = sum(context.metadata.get(f, {}).get("size", 0) for f in removed_files)

        messages = [
            f"Deduplication: found {len(duplicates_found)} sets of duplicates",
            f"Kept {len(kept_files)}/{len(context.files)} files",
            f"Removed {len(removed_files)} duplicates ({size_saved / 1024 / 1024:.1f} MB)",
        ]

        if self.keep_variant:
            messages.append(f"Preferred variant: {self.keep_variant}")

        return ProcessorResult(
            files=kept_files,
            metadata={f: context.metadata[f] for f in kept_files if f in context.metadata},
            removed_files=removed_files,
            stats={
                "duplicate_sets": len(duplicates_found),
                "duplicates_removed": len(removed_files),
                "size_saved_bytes": size_saved,
                "keep_strategy": self.keep_strategy,
            },
            messages=messages,
        )
