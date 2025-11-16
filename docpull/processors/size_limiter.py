"""Size limiter processor - enforces file and total size limits."""

from pathlib import Path
from typing import Optional

from .base import BaseProcessor, ProcessorContext, ProcessorResult


class SizeLimiter(BaseProcessor):
    """Enforce file size and total size limits.

    Removes or truncates files that exceed size limits, preventing
    excessively large downloads.

    Configuration:
        max_file_size: Maximum size per file in bytes (or use '100kb', '1mb')
        max_total_size: Maximum total size across all files
        action: What to do with oversized files ('skip', 'truncate', 'warn')
        truncate_marker: Text to add when truncating (default: '\\n\\n[Content truncated]')

    Example:
        # Skip files larger than 200 KB
        SizeLimiter({'max_file_size': '200kb', 'action': 'skip'})

        # Limit total download to 500 MB
        SizeLimiter({'max_total_size': '500mb'})
    """

    def __init__(self, config: Optional[dict[str, any]] = None):
        """Initialize size limiter.

        Args:
            config: Configuration dict
        """
        super().__init__(config)

        self.max_file_size: Optional[int] = self._parse_size(self.config.get("max_file_size"))
        self.max_total_size: Optional[int] = self._parse_size(self.config.get("max_total_size"))
        self.action: str = self.config.get("action", "skip")
        self.truncate_marker: str = self.config.get(
            "truncate_marker", "\n\n[Content truncated due to size limit]"
        )

        if self.action not in ("skip", "truncate", "warn"):
            raise ValueError(f"Invalid action: {self.action}. Must be 'skip', 'truncate', or 'warn'")

    def _parse_size(self, size_spec: Optional[any]) -> Optional[int]:
        """Parse size specification to bytes.

        Args:
            size_spec: Size as int (bytes) or str ('100kb', '1mb', '5gb')

        Returns:
            Size in bytes or None
        """
        if size_spec is None:
            return None

        if isinstance(size_spec, int):
            return size_spec

        if isinstance(size_spec, str):
            size_spec = size_spec.lower().strip()

            # Parse unit
            multipliers = {
                "b": 1,
                "kb": 1024,
                "mb": 1024 * 1024,
                "gb": 1024 * 1024 * 1024,
            }

            for unit, multiplier in multipliers.items():
                if size_spec.endswith(unit):
                    try:
                        number = float(size_spec[: -len(unit)])
                        return int(number * multiplier)
                    except ValueError as err:
                        raise ValueError(f"Invalid size specification: {size_spec}") from err

            # No unit, assume bytes
            try:
                return int(size_spec)
            except ValueError as err:
                raise ValueError(f"Invalid size specification: {size_spec}") from err

        raise ValueError(f"Invalid size specification type: {type(size_spec)}")

    def get_file_size(self, file_path: Path) -> int:
        """Get size of file in bytes.

        Args:
            file_path: Path to file

        Returns:
            Size in bytes
        """
        try:
            return file_path.stat().st_size
        except Exception as e:
            self.logger.warning(f"Could not get size of {file_path}: {e}")
            return 0

    def truncate_file(self, file_path: Path, max_size: int) -> None:
        """Truncate file to maximum size.

        Args:
            file_path: Path to file to truncate
            max_size: Maximum size in bytes
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read(max_size)

            # Find last complete line
            last_newline = content.rfind("\n")
            if last_newline > 0:
                content = content[:last_newline]

            # Add truncation marker
            content += self.truncate_marker

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.logger.debug(f"Truncated {file_path} to {max_size} bytes")

        except Exception as e:
            self.logger.error(f"Failed to truncate {file_path}: {e}")

    def process(self, context: ProcessorContext) -> ProcessorResult:
        """Enforce size limits.

        Args:
            context: ProcessorContext with files to check

        Returns:
            ProcessorResult with size-limited files
        """
        if not self.max_file_size and not self.max_total_size:
            return ProcessorResult(files=context.files, metadata=context.metadata, stats={"enabled": False})

        kept_files: list[Path] = []
        removed_files: list[Path] = []
        truncated_files: list[Path] = []
        total_size = 0
        total_size_limit_reached = False

        for file_path in context.files:
            if self.should_skip(file_path, context):
                kept_files.append(file_path)
                continue

            file_size = self.get_file_size(file_path)

            # Check file size limit
            if self.max_file_size and file_size > self.max_file_size:
                if self.action == "skip":
                    removed_files.append(file_path)
                    self.logger.debug(
                        f"Skipping {file_path} (size: {file_size / 1024:.1f} KB, "
                        f"limit: {self.max_file_size / 1024:.1f} KB)"
                    )
                    continue
                elif self.action == "truncate":
                    self.truncate_file(file_path, self.max_file_size)
                    truncated_files.append(file_path)
                    file_size = self.get_file_size(file_path)
                elif self.action == "warn":
                    self.logger.warning(
                        f"File {file_path} exceeds size limit "
                        f"({file_size / 1024:.1f} KB > {self.max_file_size / 1024:.1f} KB)"
                    )

            # Check total size limit
            if self.max_total_size and total_size + file_size > self.max_total_size:
                if not total_size_limit_reached:
                    self.logger.warning(
                        f"Total size limit reached ({self.max_total_size / 1024 / 1024:.1f} MB). "
                        f"Skipping remaining files."
                    )
                    total_size_limit_reached = True
                removed_files.append(file_path)
                continue

            kept_files.append(file_path)
            total_size += file_size

        # Calculate stats
        size_saved = sum(self.get_file_size(f) for f in removed_files)

        messages = []
        if self.max_file_size:
            messages.append(f"Max file size: {self.max_file_size / 1024:.1f} KB " f"(action: {self.action})")
        if self.max_total_size:
            messages.append(
                f"Max total size: {self.max_total_size / 1024 / 1024:.1f} MB "
                f"(used: {total_size / 1024 / 1024:.1f} MB)"
            )

        messages.append(f"Removed {len(removed_files)} files, " f"truncated {len(truncated_files)} files")

        return ProcessorResult(
            files=kept_files,
            metadata={f: context.metadata.get(f, {}) for f in kept_files},
            removed_files=removed_files,
            stats={
                "removed": len(removed_files),
                "truncated": len(truncated_files),
                "size_saved_bytes": size_saved,
                "total_size_bytes": total_size,
            },
            messages=messages,
        )
