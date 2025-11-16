"""Orchestrator for coordinating all v1.2.0 features.

This module wires together all the new features (processors, formatters,
indexer, metadata, cache, hooks, git, archive) into a cohesive pipeline.
"""

import logging
from pathlib import Path
from typing import Optional

from .archive import Archiver
from .cache import CacheManager
from .config import FetcherConfig
from .hooks import HookManager, HookType
from .indexer import DocIndexer
from .metadata import MetadataExtractor
from .processors import (
    ContentFilter,
    Deduplicator,
    LanguageFilter,
    ProcessorContext,
    ProcessorPipeline,
    SizeLimiter,
)
from .vcs import GitIntegration

logger = logging.getLogger(__name__)


class DocpullOrchestrator:
    """Orchestrates the complete documentation fetching and processing pipeline."""

    def __init__(self, config: FetcherConfig):
        """Initialize orchestrator with configuration.

        Args:
            config: FetcherConfig instance with all settings
        """
        self.config = config

        # Initialize components
        self.cache: Optional[CacheManager] = None
        if config.incremental or config.update_only_changed:
            self.cache = CacheManager(config.cache_dir)

        self.hooks: Optional[HookManager] = None
        if config.post_process_hook:
            self.hooks = HookManager()
            self.hooks.load_from_file(Path(config.post_process_hook))

    def build_processor_pipeline(self) -> ProcessorPipeline:
        """Build the post-processing pipeline based on config.

        Returns:
            ProcessorPipeline with configured processors
        """
        processors = []

        # Language filtering
        if self.config.language or self.config.exclude_languages:
            lang_config = {}
            if self.config.language:
                lang_config["include"] = [self.config.language]
            if self.config.exclude_languages:
                lang_config["exclude"] = self.config.exclude_languages
            processors.append(LanguageFilter(lang_config))

        # Deduplication
        if self.config.deduplicate:
            dedup_config = {
                "enabled": True,
                "keep_variant": self.config.keep_variant,
            }
            processors.append(Deduplicator(dedup_config))

        # Size limits
        if self.config.max_file_size or self.config.max_total_size:
            size_config = {}
            if self.config.max_file_size:
                size_config["max_file_size"] = self.config.max_file_size
            if self.config.max_total_size:
                size_config["max_total_size"] = self.config.max_total_size
            processors.append(SizeLimiter(size_config))

        # Content filtering
        if self.config.exclude_sections:
            content_config = {
                "exclude_sections": self.config.exclude_sections,
            }
            processors.append(ContentFilter(content_config))

        return ProcessorPipeline(processors)

    def post_process(self, files: list[Path], metadata: Optional[dict[Path, dict]] = None) -> list[Path]:
        """Run post-processing pipeline on fetched files.

        Args:
            files: List of fetched file paths
            metadata: Optional metadata dict for files

        Returns:
            List of files after processing
        """
        if not files:
            logger.info("No files to process")
            return files

        logger.info(f"Starting post-processing for {len(files)} files")

        # Build context
        context = ProcessorContext(files=files, metadata=metadata or {}, output_dir=self.config.output_dir)

        # Run hooks (pre-processing)
        if self.hooks:
            hook_context = {"files": files, "config": self.config}
            hook_result = self.hooks.execute_hooks(HookType.POST_PROCESS, hook_context)
            if not hook_result.should_continue:
                logger.warning("Post-process hooks returned false, skipping processing")
                return files

        # Run processor pipeline
        pipeline = self.build_processor_pipeline()
        if pipeline.processors:
            context = pipeline.run(context)
            logger.info(f"Post-processing complete: {len(context.files)} files remaining")
        else:
            logger.info("No processors configured, skipping post-processing")

        return context.files

    def generate_index(self, files: list[Path]):
        """Generate index files if configured.

        Args:
            files: List of files to index
        """
        if not self.config.create_index:
            return

        logger.info("Generating indexes...")

        indexer = DocIndexer(
            output_dir=self.config.output_dir,
            styles=["tree", "toc", "categories", "stats"],
            include_stats=True,
            per_directory=False,
        )

        result = indexer.create_all_indexes(files)
        logger.info(f"Created {len(result['directory_indexes']) + 1} index files")

    def extract_metadata(self, files: list[Path]):
        """Extract metadata if configured.

        Args:
            files: List of files to extract metadata from
        """
        if not self.config.extract_metadata:
            return

        logger.info("Extracting metadata...")

        extractor = MetadataExtractor(self.config.output_dir)
        metadata_file = extractor.save_metadata()
        logger.info(f"Saved metadata to {metadata_file}")

    def commit_to_git(self):
        """Commit changes to git if configured."""
        if not self.config.git_commit:
            return

        logger.info("Committing changes to git...")

        git = GitIntegration(self.config.output_dir)

        if not git._is_git_repo():
            logger.warning(f"{self.config.output_dir} is not a git repository, skipping git commit")
            return

        success = git.auto_commit(
            message_template=self.config.git_message, patterns=[str(self.config.output_dir)]
        )

        if success:
            logger.info("Successfully committed changes to git")
        else:
            logger.warning("Git commit failed or no changes to commit")

    def create_archive(self):
        """Create archive if configured."""
        if not self.config.archive:
            return

        logger.info(f"Creating archive (format: {self.config.archive_format})...")

        archiver = Archiver(self.config.output_dir)
        archive_path = archiver.create_archive(
            format=self.config.archive_format,
            include_patterns=["**/*.md", "**/*.json", "**/INDEX.md", "**/metadata.json"],
        )

        logger.info(f"Created archive: {archive_path}")

    def run_post_fetch_pipeline(
        self, files: list[Path], metadata: Optional[dict[Path, dict]] = None
    ) -> list[Path]:
        """Run the complete post-fetch pipeline.

        This is the main orchestration method that runs all post-processing steps.

        Args:
            files: List of fetched files
            metadata: Optional metadata for files

        Returns:
            List of processed files
        """
        logger.info("=" * 70)
        logger.info("Running post-fetch pipeline")
        logger.info("=" * 70)

        # 1. Post-processing (filters, dedup, size limits)
        processed_files = self.post_process(files, metadata)

        # 2. Index generation
        self.generate_index(processed_files)

        # 3. Metadata extraction
        self.extract_metadata(processed_files)

        # 4. Git commit
        self.commit_to_git()

        # 5. Archive creation
        self.create_archive()

        logger.info("=" * 70)
        logger.info("Post-fetch pipeline complete")
        logger.info("=" * 70)

        return processed_files


def create_orchestrator(config: FetcherConfig) -> DocpullOrchestrator:
    """Factory function to create an orchestrator.

    Args:
        config: FetcherConfig instance

    Returns:
        DocpullOrchestrator instance
    """
    return DocpullOrchestrator(config)
