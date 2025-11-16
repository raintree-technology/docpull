"""Archive creation for documentation bundles."""

import logging
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Archiver:
    """Create compressed archives of documentation."""

    def __init__(self, output_dir: Path):
        """Initialize archiver.

        Args:
            output_dir: Directory containing docs to archive
        """
        self.output_dir = Path(output_dir)

    def create_tarball(
        self,
        archive_name: Optional[str] = None,
        compression: str = "gz",
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ) -> Path:
        """Create a tar.gz archive.

        Args:
            archive_name: Archive filename (default: docs-{date}.tar.gz)
            compression: Compression type ('gz', 'bz2', 'xz', '')
            include_patterns: Glob patterns to include
            exclude_patterns: Glob patterns to exclude

        Returns:
            Path to created archive
        """
        # Generate archive name
        if not archive_name:
            date_str = datetime.now().strftime("%Y%m%d")
            dir_name = self.output_dir.name
            ext = f".tar.{compression}" if compression else ".tar"
            archive_name = f"{dir_name}-{date_str}{ext}"

        archive_path = self.output_dir.parent / archive_name

        # Determine compression mode
        mode_map = {
            "gz": "w:gz",
            "bz2": "w:bz2",
            "xz": "w:xz",
            "": "w",
        }
        mode = mode_map.get(compression, "w:gz")

        # Collect files
        files = self._collect_files(include_patterns, exclude_patterns)

        logger.info(f"Creating tarball with {len(files)} files")

        # Create archive
        with tarfile.open(archive_path, mode) as tar:
            for file_path in files:
                # Get path relative to output dir
                arcname = file_path.relative_to(self.output_dir.parent)
                tar.add(file_path, arcname=arcname)

        size_mb = archive_path.stat().st_size / 1024 / 1024
        logger.info(f"Created archive: {archive_path} ({size_mb:.1f} MB)")

        return archive_path

    def create_zip(
        self,
        archive_name: Optional[str] = None,
        compression: int = zipfile.ZIP_DEFLATED,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ) -> Path:
        """Create a ZIP archive.

        Args:
            archive_name: Archive filename (default: docs-{date}.zip)
            compression: ZIP compression type
            include_patterns: Glob patterns to include
            exclude_patterns: Glob patterns to exclude

        Returns:
            Path to created archive
        """
        # Generate archive name
        if not archive_name:
            date_str = datetime.now().strftime("%Y%m%d")
            dir_name = self.output_dir.name
            archive_name = f"{dir_name}-{date_str}.zip"

        archive_path = self.output_dir.parent / archive_name

        # Collect files
        files = self._collect_files(include_patterns, exclude_patterns)

        logger.info(f"Creating ZIP with {len(files)} files")

        # Create archive
        with zipfile.ZipFile(archive_path, "w", compression) as zf:
            for file_path in files:
                # Get path relative to output dir
                arcname = file_path.relative_to(self.output_dir.parent)
                zf.write(file_path, arcname=arcname)

        size_mb = archive_path.stat().st_size / 1024 / 1024
        logger.info(f"Created archive: {archive_path} ({size_mb:.1f} MB)")

        return archive_path

    def _collect_files(
        self, include_patterns: Optional[list[str]] = None, exclude_patterns: Optional[list[str]] = None
    ) -> list[Path]:
        """Collect files to include in archive.

        Args:
            include_patterns: Glob patterns to include
            exclude_patterns: Glob patterns to exclude

        Returns:
            List of file paths
        """
        include_patterns = include_patterns or ["**/*"]
        exclude_patterns = exclude_patterns or []

        files = set()

        # Include files matching patterns
        for pattern in include_patterns:
            for file_path in self.output_dir.glob(pattern):
                if file_path.is_file():
                    files.add(file_path)

        # Exclude files matching patterns
        for pattern in exclude_patterns:
            for file_path in list(files):
                if file_path.match(pattern):
                    files.remove(file_path)

        return sorted(files)

    def create_archive(
        self,
        archive_format: str = "tar.gz",
        archive_name: Optional[str] = None,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ) -> Path:
        """Create an archive in the specified format.

        Args:
            archive_format: Archive format ('tar.gz', 'tar.bz2', 'tar.xz', 'zip')
            archive_name: Optional archive name
            include_patterns: Patterns to include
            exclude_patterns: Patterns to exclude

        Returns:
            Path to created archive

        Raises:
            ValueError: If format is unknown
        """
        if archive_format in ("tar.gz", "tgz"):
            return self.create_tarball(archive_name, "gz", include_patterns, exclude_patterns)
        elif archive_format in ("tar.bz2", "tbz2"):
            return self.create_tarball(archive_name, "bz2", include_patterns, exclude_patterns)
        elif archive_format in ("tar.xz", "txz"):
            return self.create_tarball(archive_name, "xz", include_patterns, exclude_patterns)
        elif archive_format == "tar":
            return self.create_tarball(archive_name, "", include_patterns, exclude_patterns)
        elif archive_format == "zip":
            return self.create_zip(archive_name, zipfile.ZIP_DEFLATED, include_patterns, exclude_patterns)
        else:
            raise ValueError(
                f"Unknown archive format: {archive_format}. "
                f"Supported formats: tar.gz, tar.bz2, tar.xz, tar, zip"
            )
