"""Tests for sources configuration module."""

from pathlib import Path

import pytest
import yaml

from docpull.sources_config import SourcesConfiguration


class TestSourcesConfiguration:
    """Test SourcesConfiguration."""

    def test_load_from_yaml(self, tmp_path):
        """Test loading configuration from YAML."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  anthropic:
    url: https://docs.anthropic.com
    language: en
    create_index: true

  aptos:
    url: https://aptos.dev
    deduplicate: true
    keep_variant: mainnet

output_dir: ./docs
rate_limit: 0.5
git_commit: true
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        assert len(config.sources) == 2
        assert "anthropic" in config.sources
        assert "aptos" in config.sources
        assert config.sources["anthropic"]["url"] == "https://docs.anthropic.com"
        assert config.sources["anthropic"]["language"] == "en"
        assert config.sources["aptos"]["deduplicate"] is True
        assert config.global_config.output_dir == Path("./docs")
        assert config.global_config.rate_limit == 0.5
        assert config.global_config.git_commit is True

    def test_load_minimal_config(self, tmp_path):
        """Test loading minimal configuration."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        assert len(config.sources) == 1
        assert config.sources["test"]["url"] == "https://example.com"

    def test_per_source_output_dir(self, tmp_path):
        """Test per-source output directory."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  source1:
    url: https://example.com
    output_dir: ./custom-output

  source2:
    url: https://example2.com

output_dir: ./docs
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        assert config.sources["source1"]["output_dir"] == "./custom-output"
        assert "output_dir" not in config.sources["source2"]
        assert config.global_config.output_dir == Path("./docs")

    def test_validate_sources(self, tmp_path):
        """Test source validation."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
    language: en
    max_file_size: 200kb
    deduplicate: true
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        # Should load without errors
        assert len(config.sources) == 1

    def test_merge_with_global_config(self, tmp_path):
        """Test merging source config with global config."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
    language: en

output_dir: ./docs
rate_limit: 1.0
log_level: DEBUG
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        # Global config should have settings
        assert config.global_config.rate_limit == 1.0
        assert config.global_config.log_level == "DEBUG"

        # Source should have its own settings
        assert config.sources["test"]["language"] == "en"

    def test_multiple_sources_different_settings(self, tmp_path):
        """Test multiple sources with different settings."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  anthropic:
    url: https://docs.anthropic.com
    language: en
    create_index: true
    extract_metadata: true

  aptos:
    url: https://aptos.dev
    deduplicate: true
    keep_variant: mainnet
    include_paths:
      - "build/*"

  shelby:
    url: https://docs.shelby.xyz
    max_file_size: 200kb
    exclude_sections:
      - Examples
      - Changelog

output_dir: ./docs
git_commit: true
archive: true
archive_format: tar.gz
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        assert len(config.sources) == 3

        # Check anthropic settings
        assert config.sources["anthropic"]["language"] == "en"
        assert config.sources["anthropic"]["create_index"] is True

        # Check aptos settings
        assert config.sources["aptos"]["deduplicate"] is True
        assert config.sources["aptos"]["keep_variant"] == "mainnet"
        assert "build/*" in config.sources["aptos"]["include_paths"]

        # Check shelby settings
        assert config.sources["shelby"]["max_file_size"] == "200kb"
        assert "Examples" in config.sources["shelby"]["exclude_sections"]

        # Check global settings
        assert config.global_config.git_commit is True
        assert config.global_config.archive is True
        assert config.global_config.archive_format == "tar.gz"

    def test_generate_template(self, tmp_path):
        """Test generating configuration template."""
        output_file = tmp_path / "template.yaml"

        SourcesConfiguration.generate_template(output_file)

        assert output_file.exists()

        # Load and verify template
        config = SourcesConfiguration.from_yaml(output_file)
        assert "example" in config.sources

    def test_invalid_yaml(self, tmp_path):
        """Test handling invalid YAML."""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("invalid: yaml: syntax:")

        with pytest.raises((yaml.YAMLError, ValueError)):
            SourcesConfiguration.from_yaml(yaml_file)

    def test_missing_sources_key(self, tmp_path):
        """Test handling missing sources key."""
        yaml_file = tmp_path / "missing.yaml"
        yaml_file.write_text(
            """
output_dir: ./docs
rate_limit: 0.5
"""
        )

        with pytest.raises(ValueError):
            SourcesConfiguration.from_yaml(yaml_file)

    def test_empty_sources(self, tmp_path):
        """Test handling empty sources."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text(
            """
sources: {}
"""
        )

        with pytest.raises(ValueError):
            SourcesConfiguration.from_yaml(yaml_file)


class TestSourceConfigValidation:
    """Test source configuration validation."""

    def test_valid_url(self, tmp_path):
        """Test valid URL validation."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)
        assert config.sources["test"]["url"] == "https://example.com"

    def test_missing_url(self, tmp_path):
        """Test missing URL validation."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    language: en
"""
        )

        with pytest.raises(ValueError):
            SourcesConfiguration.from_yaml(yaml_file)

    def test_valid_language_codes(self, tmp_path):
        """Test valid language codes."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
    language: en
    exclude_languages:
      - fr
      - de
      - ja
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)
        assert config.sources["test"]["language"] == "en"
        assert "fr" in config.sources["test"]["exclude_languages"]

    def test_valid_size_formats(self, tmp_path):
        """Test valid size format validation."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
    max_file_size: 200kb
    max_total_size: 500mb
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)
        assert config.sources["test"]["max_file_size"] == "200kb"
        assert config.sources["test"]["max_total_size"] == "500mb"

    def test_valid_archive_formats(self, tmp_path):
        """Test valid archive format validation."""
        for fmt in ["tar.gz", "tar.bz2", "tar.xz", "zip"]:
            yaml_file = tmp_path / f"{fmt}.yaml"
            yaml_file.write_text(
                f"""
sources:
  test:
    url: https://example.com

archive: true
archive_format: {fmt}
"""
            )

            config = SourcesConfiguration.from_yaml(yaml_file)
            assert config.global_config.archive_format == fmt

    def test_boolean_flags(self, tmp_path):
        """Test boolean flag handling."""
        yaml_file = tmp_path / "sources.yaml"
        yaml_file.write_text(
            """
sources:
  test:
    url: https://example.com
    deduplicate: true
    create_index: true
    extract_metadata: true
    incremental: true

git_commit: true
archive: true
dry_run: true
"""
        )

        config = SourcesConfiguration.from_yaml(yaml_file)

        # Source-level flags
        assert config.sources["test"]["deduplicate"] is True
        assert config.sources["test"]["create_index"] is True
        assert config.sources["test"]["extract_metadata"] is True
        assert config.sources["test"]["incremental"] is True

        # Global flags
        assert config.global_config.git_commit is True
        assert config.global_config.archive is True
        assert config.global_config.dry_run is True
