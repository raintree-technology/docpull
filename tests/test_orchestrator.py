"""Tests for the orchestrator module."""

from unittest.mock import MagicMock, patch

from docpull.config import FetcherConfig
from docpull.orchestrator import DocpullOrchestrator, create_orchestrator


class TestOrchestratorCreation:
    """Test orchestrator creation and initialization."""

    def test_create_orchestrator(self):
        """Test orchestrator factory function."""
        config = FetcherConfig(output_dir="./test-docs")
        orchestrator = create_orchestrator(config)

        assert orchestrator is not None
        assert isinstance(orchestrator, DocpullOrchestrator)
        assert orchestrator.config == config

    def test_orchestrator_init_basic(self):
        """Test basic orchestrator initialization."""
        config = FetcherConfig(output_dir="./test-docs")
        orchestrator = DocpullOrchestrator(config)

        assert orchestrator.config == config
        assert orchestrator.cache is None
        assert orchestrator.hooks is None

    def test_orchestrator_init_with_cache(self):
        """Test orchestrator initialization with cache enabled."""
        config = FetcherConfig(output_dir="./test-docs", incremental=True)
        orchestrator = DocpullOrchestrator(config)

        assert orchestrator.cache is not None

    def test_orchestrator_init_with_hooks(self, tmp_path):
        """Test orchestrator initialization with hooks."""
        hook_file = tmp_path / "hook.py"
        hook_file.write_text("# Empty hook file")

        config = FetcherConfig(output_dir="./test-docs", post_process_hook=str(hook_file))
        orchestrator = DocpullOrchestrator(config)

        assert orchestrator.hooks is not None


class TestProcessorPipeline:
    """Test processor pipeline building."""

    def test_build_empty_pipeline(self):
        """Test building pipeline with no processors."""
        config = FetcherConfig(output_dir="./test-docs")
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert pipeline is not None
        assert len(pipeline.processors) == 0

    def test_build_pipeline_with_language_filter(self):
        """Test building pipeline with language filter."""
        config = FetcherConfig(output_dir="./test-docs", language="en")
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert len(pipeline.processors) >= 1

    def test_build_pipeline_with_deduplicator(self):
        """Test building pipeline with deduplicator."""
        config = FetcherConfig(output_dir="./test-docs", deduplicate=True)
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert len(pipeline.processors) >= 1

    def test_build_pipeline_with_size_limiter(self):
        """Test building pipeline with size limiter."""
        config = FetcherConfig(output_dir="./test-docs", max_file_size="200kb")
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert len(pipeline.processors) >= 1

    def test_build_pipeline_with_content_filter(self):
        """Test building pipeline with content filter."""
        config = FetcherConfig(output_dir="./test-docs", exclude_sections=["Examples", "Changelog"])
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert len(pipeline.processors) >= 1

    def test_build_pipeline_with_all_processors(self):
        """Test building pipeline with all processors."""
        config = FetcherConfig(
            output_dir="./test-docs",
            language="en",
            deduplicate=True,
            max_file_size="200kb",
            exclude_sections=["Examples"],
        )
        orchestrator = DocpullOrchestrator(config)

        pipeline = orchestrator.build_processor_pipeline()

        assert len(pipeline.processors) == 4


class TestPostProcessing:
    """Test post-processing functionality."""

    def test_post_process_empty_files(self):
        """Test post-processing with no files."""
        config = FetcherConfig(output_dir="./test-docs")
        orchestrator = DocpullOrchestrator(config)

        result = orchestrator.post_process([])

        assert result == []

    def test_post_process_with_files(self, tmp_path):
        """Test post-processing with files."""
        config = FetcherConfig(output_dir=str(tmp_path))
        orchestrator = DocpullOrchestrator(config)

        # Create test files
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test Content")

        files = [test_file]
        result = orchestrator.post_process(files)

        assert len(result) == 1
        assert result[0] == test_file


class TestIndexGeneration:
    """Test index generation."""

    @patch("docpull.orchestrator.DocIndexer")
    def test_generate_index_disabled(self, mock_indexer):
        """Test index generation when disabled."""
        config = FetcherConfig(output_dir="./test-docs", create_index=False)
        orchestrator = DocpullOrchestrator(config)

        orchestrator.generate_index([])

        mock_indexer.assert_not_called()

    @patch("docpull.orchestrator.DocIndexer")
    def test_generate_index_enabled(self, mock_indexer, tmp_path):
        """Test index generation when enabled."""
        config = FetcherConfig(output_dir=str(tmp_path), create_index=True)
        orchestrator = DocpullOrchestrator(config)

        # Mock indexer
        mock_instance = MagicMock()
        mock_instance.create_all_indexes.return_value = {
            "main_index": tmp_path / "INDEX.md",
            "directory_indexes": [],
        }
        mock_indexer.return_value = mock_instance

        # Create test file
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test")

        orchestrator.generate_index([test_file])

        mock_indexer.assert_called_once()
        mock_instance.create_all_indexes.assert_called_once()


class TestMetadataExtraction:
    """Test metadata extraction."""

    @patch("docpull.orchestrator.MetadataExtractor")
    def test_extract_metadata_disabled(self, mock_extractor):
        """Test metadata extraction when disabled."""
        config = FetcherConfig(output_dir="./test-docs", extract_metadata=False)
        orchestrator = DocpullOrchestrator(config)

        orchestrator.extract_metadata([])

        mock_extractor.assert_not_called()

    @patch("docpull.orchestrator.MetadataExtractor")
    def test_extract_metadata_enabled(self, mock_extractor, tmp_path):
        """Test metadata extraction when enabled."""
        config = FetcherConfig(output_dir=str(tmp_path), extract_metadata=True)
        orchestrator = DocpullOrchestrator(config)

        # Mock extractor
        mock_instance = MagicMock()
        mock_instance.save_metadata.return_value = tmp_path / "metadata.json"
        mock_extractor.return_value = mock_instance

        test_file = tmp_path / "test.md"
        test_file.write_text("# Test")

        orchestrator.extract_metadata([test_file])

        mock_extractor.assert_called_once()
        mock_instance.save_metadata.assert_called_once()


class TestGitIntegration:
    """Test git integration."""

    @patch("docpull.orchestrator.GitIntegration")
    def test_git_commit_disabled(self, mock_git):
        """Test git commit when disabled."""
        config = FetcherConfig(output_dir="./test-docs", git_commit=False)
        orchestrator = DocpullOrchestrator(config)

        orchestrator.commit_to_git()

        mock_git.assert_not_called()

    @patch("docpull.orchestrator.GitIntegration")
    def test_git_commit_enabled_not_repo(self, mock_git, tmp_path):
        """Test git commit when enabled but not a git repo."""
        config = FetcherConfig(output_dir=str(tmp_path), git_commit=True)
        orchestrator = DocpullOrchestrator(config)

        # Mock git instance
        mock_instance = MagicMock()
        mock_instance._is_git_repo.return_value = False
        mock_git.return_value = mock_instance

        orchestrator.commit_to_git()

        mock_git.assert_called_once()
        mock_instance.auto_commit.assert_not_called()

    @patch("docpull.orchestrator.GitIntegration")
    def test_git_commit_enabled_is_repo(self, mock_git, tmp_path):
        """Test git commit when enabled and is git repo."""
        config = FetcherConfig(output_dir=str(tmp_path), git_commit=True, git_message="Test commit")
        orchestrator = DocpullOrchestrator(config)

        # Mock git instance
        mock_instance = MagicMock()
        mock_instance._is_git_repo.return_value = True
        mock_instance.auto_commit.return_value = True
        mock_git.return_value = mock_instance

        orchestrator.commit_to_git()

        mock_git.assert_called_once()
        mock_instance.auto_commit.assert_called_once()


class TestArchiveCreation:
    """Test archive creation."""

    @patch("docpull.orchestrator.Archiver")
    def test_archive_disabled(self, mock_archiver):
        """Test archive creation when disabled."""
        config = FetcherConfig(output_dir="./test-docs", archive=False)
        orchestrator = DocpullOrchestrator(config)

        orchestrator.create_archive()

        mock_archiver.assert_not_called()

    @patch("docpull.orchestrator.Archiver")
    def test_archive_enabled(self, mock_archiver, tmp_path):
        """Test archive creation when enabled."""
        config = FetcherConfig(output_dir=str(tmp_path), archive=True, archive_format="tar.gz")
        orchestrator = DocpullOrchestrator(config)

        # Mock archiver
        mock_instance = MagicMock()
        mock_instance.create_archive.return_value = tmp_path / "archive.tar.gz"
        mock_archiver.return_value = mock_instance

        orchestrator.create_archive()

        mock_archiver.assert_called_once()
        mock_instance.create_archive.assert_called_once_with(
            format="tar.gz", include_patterns=["**/*.md", "**/*.json", "**/INDEX.md", "**/metadata.json"]
        )


class TestPostFetchPipeline:
    """Test complete post-fetch pipeline."""

    @patch("docpull.orchestrator.Archiver")
    @patch("docpull.orchestrator.GitIntegration")
    @patch("docpull.orchestrator.MetadataExtractor")
    @patch("docpull.orchestrator.DocIndexer")
    def test_run_post_fetch_pipeline(self, mock_indexer, mock_extractor, mock_git, mock_archiver, tmp_path):
        """Test running complete post-fetch pipeline."""
        config = FetcherConfig(
            output_dir=str(tmp_path),
            create_index=True,
            extract_metadata=True,
            git_commit=False,  # Disable to avoid git checks
            archive=False,
        )
        orchestrator = DocpullOrchestrator(config)

        # Create test file
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test Content")

        # Mock components
        mock_indexer_instance = MagicMock()
        mock_indexer_instance.create_all_indexes.return_value = {
            "main_index": tmp_path / "INDEX.md",
            "directory_indexes": [],
        }
        mock_indexer.return_value = mock_indexer_instance

        mock_extractor_instance = MagicMock()
        mock_extractor_instance.save_metadata.return_value = tmp_path / "metadata.json"
        mock_extractor.return_value = mock_extractor_instance

        # Run pipeline
        result = orchestrator.run_post_fetch_pipeline([test_file])

        # Verify pipeline ran
        assert len(result) >= 1
        mock_indexer.assert_called_once()
        mock_extractor.assert_called_once()
