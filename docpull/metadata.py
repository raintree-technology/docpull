"""Metadata extraction and aggregation for documentation."""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MetadataExtractor:
    """Extract and aggregate metadata from documentation files."""

    def __init__(self, output_dir: Path):
        """Initialize metadata extractor.

        Args:
            output_dir: Root directory containing docs
        """
        self.output_dir = Path(output_dir)

    def extract_from_file(self, file_path: Path) -> dict[str, any]:
        """Extract metadata from a single file.

        Args:
            file_path: Path to file

        Returns:
            Metadata dict with title, url, size, etc.
        """
        metadata = {
            "path": str(file_path),
            "size": 0,
            "word_count": 0,
            "title": None,
            "url": None,
            "last_updated": None,
            "category": None,
            "headers": [],
        }

        try:
            # Get file stats
            stat = file_path.stat()
            metadata["size"] = stat.st_size
            metadata["last_updated"] = datetime.fromtimestamp(stat.st_mtime).isoformat()

            # Determine category from path
            try:
                rel_path = file_path.relative_to(self.output_dir)
                if rel_path.parent != Path("."):
                    metadata["category"] = str(rel_path.parent)
            except ValueError:
                pass

            # Extract from content
            if file_path.suffix.lower() in (".md", ".markdown"):
                with open(file_path, encoding="utf-8") as f:
                    content = f.read()

                # Extract YAML frontmatter
                frontmatter_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
                if frontmatter_match:
                    frontmatter = frontmatter_match.group(1)

                    # Parse URL
                    url_match = re.search(r"^url:\s*(.+)$", frontmatter, re.MULTILINE)
                    if url_match:
                        metadata["url"] = url_match.group(1).strip()

                    # Parse title from frontmatter
                    title_match = re.search(r"^title:\s*(.+)$", frontmatter, re.MULTILINE)
                    if title_match:
                        metadata["title"] = title_match.group(1).strip()

                # Extract title from first H1 if not in frontmatter
                if not metadata["title"]:
                    h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
                    if h1_match:
                        metadata["title"] = h1_match.group(1).strip()

                # Extract all headers
                for match in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE):
                    level = len(match.group(1))
                    title = match.group(2).strip()
                    metadata["headers"].append({"level": level, "title": title})

                # Word count
                text = re.sub(r"```.*?```", "", content, flags=re.DOTALL)  # Remove code blocks
                text = re.sub(r"[#*`\[\]()]", "", text)  # Remove markdown
                words = text.split()
                metadata["word_count"] = len(words)

        except Exception as e:
            logger.warning(f"Could not extract metadata from {file_path}: {e}")

        return metadata

    def extract_from_directory(self, include_patterns: Optional[list[str]] = None) -> list[dict[str, any]]:
        """Extract metadata from all files in output directory.

        Args:
            include_patterns: Optional glob patterns to include

        Returns:
            List of metadata dicts
        """
        patterns = include_patterns or ["**/*.md", "**/*.markdown"]
        all_metadata = []

        for pattern in patterns:
            for file_path in self.output_dir.glob(pattern):
                if file_path.is_file():
                    metadata = self.extract_from_file(file_path)
                    all_metadata.append(metadata)

        return all_metadata

    def aggregate_stats(self, all_metadata: list[dict[str, any]]) -> dict[str, any]:
        """Aggregate statistics from metadata list.

        Args:
            all_metadata: List of file metadata

        Returns:
            Aggregated stats dict
        """
        stats = {
            "total_files": len(all_metadata),
            "total_size": sum(m["size"] for m in all_metadata),
            "total_words": sum(m["word_count"] for m in all_metadata),
            "categories": {},
            "file_types": {},
        }

        # Category stats
        for metadata in all_metadata:
            category = metadata.get("category") or "Root"
            if category not in stats["categories"]:
                stats["categories"][category] = {
                    "count": 0,
                    "size": 0,
                }
            stats["categories"][category]["count"] += 1
            stats["categories"][category]["size"] += metadata["size"]

            # File type stats
            file_path = Path(metadata["path"])
            ext = file_path.suffix or "(no extension)"
            stats["file_types"][ext] = stats["file_types"].get(ext, 0) + 1

        return stats

    def save_metadata(self, output_file: Optional[Path] = None) -> Path:
        """Extract and save metadata to JSON file.

        Args:
            output_file: Output file path (default: output_dir/metadata.json)

        Returns:
            Path to saved metadata file
        """
        output_file = output_file or (self.output_dir / "metadata.json")

        logger.info(f"Extracting metadata from {self.output_dir}")

        all_metadata = self.extract_from_directory()
        stats = self.aggregate_stats(all_metadata)

        output = {
            "generated_at": datetime.now().isoformat(),
            "output_dir": str(self.output_dir),
            "stats": stats,
            "files": all_metadata,
        }

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved metadata for {len(all_metadata)} files to {output_file}")

        return output_file
