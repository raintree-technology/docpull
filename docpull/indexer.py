"""Auto-index generation module - creates navigation indexes for documentation."""

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional, TypedDict, Union

logger = logging.getLogger(__name__)


class HeaderInfo(TypedDict):
    """Type for header information."""

    level: int
    title: str
    anchor: str


# Tree node can be either None (leaf/file) or another tree dict (directory)
TreeNode = Union[None, dict[str, "TreeNode"]]


class IndexResult(TypedDict):
    """Type for index creation result."""

    main_index: Path
    directory_indexes: list[Path]
    files_indexed: int


class DocIndexer:
    """Generate navigation indexes for downloaded documentation.

    Creates INDEX.md files with:
    - File tree structure
    - Table of contents from headers
    - Category groupings
    - Statistics (file counts, sizes)
    """

    def __init__(
        self,
        output_dir: Path,
        styles: Optional[list[str]] = None,
        include_stats: bool = True,
        per_directory: bool = False,
    ):
        """Initialize indexer.

        Args:
            output_dir: Root directory containing docs
            styles: List of index styles ('tree', 'toc', 'categories')
            include_stats: Include file statistics
            per_directory: Create per-directory indexes
        """
        self.output_dir = Path(output_dir)
        self.styles = styles or ["tree", "toc", "categories"]
        self.include_stats = include_stats
        self.per_directory = per_directory

    def extract_headers(self, file_path: Path) -> list[HeaderInfo]:
        """Extract headers from a markdown file.

        Args:
            file_path: Path to markdown file

        Returns:
            List of dicts with 'level', 'title', 'anchor'
        """
        headers: list[HeaderInfo] = []

        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    match = re.match(r"^(#{1,6})\s+(.+)$", line)
                    if match:
                        level = len(match.group(1))
                        title = match.group(2).strip()

                        # Create anchor (GitHub-style)
                        anchor = title.lower()
                        anchor = re.sub(r"[^\w\s-]", "", anchor)
                        anchor = re.sub(r"[-\s]+", "-", anchor)

                        headers.append(
                            {
                                "level": level,
                                "title": title,
                                "anchor": anchor,
                            }
                        )

        except Exception as e:
            logger.warning(f"Could not extract headers from {file_path}: {e}")

        return headers

    def categorize_files(self, files: list[Path]) -> dict[str, list[Path]]:
        """Categorize files by directory or topic.

        Args:
            files: List of file paths

        Returns:
            Dict mapping category name to list of files
        """
        categories = defaultdict(list)

        for file_path in files:
            # Get relative path from output dir
            try:
                rel_path = file_path.relative_to(self.output_dir)
            except ValueError:
                rel_path = file_path

            # Use parent directory as category
            category = str(rel_path.parent) if rel_path.parent != Path(".") else "Root"

            categories[category].append(file_path)

        return dict(categories)

    def generate_tree(self, files: list[Path], indent: str = "") -> str:
        """Generate file tree structure.

        Args:
            files: List of file paths
            indent: Current indentation

        Returns:
            Markdown file tree
        """
        lines = ["## File Tree\n"]

        # Build tree structure
        tree: dict[str, TreeNode] = {}

        for file_path in sorted(files):
            try:
                rel_path = file_path.relative_to(self.output_dir)
            except ValueError:
                rel_path = file_path

            parts = rel_path.parts
            current: dict[str, TreeNode] = tree

            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                node = current[part]
                if isinstance(node, dict):
                    current = node

            # Add file
            current[parts[-1]] = None

        # Render tree
        def render_tree(node: dict[str, TreeNode], prefix: str = "", is_last: bool = True) -> list[str]:
            result: list[str] = []
            items = sorted(node.items())

            for i, (name, children) in enumerate(items):
                is_last_item = i == len(items) - 1
                connector = "└── " if is_last_item else "├── "

                if children is None:
                    # File
                    result.append(f"{prefix}{connector}{name}")
                elif isinstance(children, dict):
                    # Directory
                    result.append(f"{prefix}{connector}**{name}/**")
                    extension = "    " if is_last_item else "│   "
                    result.extend(render_tree(children, prefix + extension, is_last_item))

            return result

        tree_lines = render_tree(tree)
        lines.append("```")
        lines.extend(tree_lines)
        lines.append("```\n")

        return "\n".join(lines)

    def generate_toc(self, files: list[Path]) -> str:
        """Generate table of contents from file headers.

        Args:
            files: List of file paths

        Returns:
            Markdown table of contents
        """
        lines = ["## Table of Contents\n"]

        for file_path in sorted(files):
            if file_path.suffix.lower() not in (".md", ".markdown"):
                continue

            try:
                rel_path = file_path.relative_to(self.output_dir)
            except ValueError:
                rel_path = file_path

            headers = self.extract_headers(file_path)

            if headers:
                # Add file link
                lines.append(f"### [{rel_path}]({rel_path})\n")

                # Add headers
                for header in headers[:5]:  # Limit to top 5 headers
                    indent = "  " * (header["level"] - 1)
                    lines.append(f"{indent}- [{header['title']}]({rel_path}#{header['anchor']})")

                lines.append("")

        return "\n".join(lines)

    def generate_categories(self, files: list[Path]) -> str:
        """Generate categorized file listing.

        Args:
            files: List of file paths

        Returns:
            Markdown categorized listing
        """
        lines = ["## Categories\n"]

        categories = self.categorize_files(files)

        for category, category_files in sorted(categories.items()):
            lines.append(f"### {category}\n")

            for file_path in sorted(category_files):
                try:
                    rel_path = file_path.relative_to(self.output_dir)
                except ValueError:
                    rel_path = file_path

                # Get file title from first header
                headers = self.extract_headers(file_path)
                title = headers[0]["title"] if headers else rel_path.stem

                lines.append(f"- [{title}]({rel_path})")

            lines.append("")

        return "\n".join(lines)

    def generate_stats(self, files: list[Path]) -> str:
        """Generate statistics about the documentation.

        Args:
            files: List of file paths

        Returns:
            Markdown statistics
        """
        lines = ["## Statistics\n"]

        total_size = 0
        file_types: defaultdict[str, int] = defaultdict(int)

        for file_path in files:
            try:
                size = file_path.stat().st_size
                total_size += size
            except Exception:
                pass

            file_types[file_path.suffix] += 1

        lines.append(f"- **Total Files:** {len(files)}")
        lines.append(f"- **Total Size:** {total_size / 1024 / 1024:.2f} MB")
        lines.append("")

        lines.append("**File Types:**\n")
        for ext, count in sorted(file_types.items(), key=lambda x: -x[1]):
            ext_name = ext if ext else "(no extension)"
            lines.append(f"- {ext_name}: {count}")

        lines.append("")

        return "\n".join(lines)

    def generate_index(self, files: list[Path], title: str = "Documentation Index") -> str:
        """Generate complete index file.

        Args:
            files: List of file paths
            title: Index title

        Returns:
            Complete markdown index
        """
        lines = [
            f"# {title}\n",
            f"*Auto-generated index for {len(files)} files*\n",
            "---\n",
        ]

        if "stats" in self.styles and self.include_stats:
            lines.append(self.generate_stats(files))

        if "tree" in self.styles:
            lines.append(self.generate_tree(files))

        if "categories" in self.styles:
            lines.append(self.generate_categories(files))

        if "toc" in self.styles:
            lines.append(self.generate_toc(files))

        lines.append("---\n")
        lines.append("*Generated by docpull indexer*\n")

        return "\n".join(lines)

    def create_index(self, files: list[Path]) -> Path:
        """Create main INDEX.md file.

        Args:
            files: List of file paths to index

        Returns:
            Path to created index file
        """
        index_path = self.output_dir / "INDEX.md"

        content = self.generate_index(files)

        with open(index_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Created index at {index_path}")

        return index_path

    def create_directory_indexes(self, files: list[Path]) -> list[Path]:
        """Create per-directory index files.

        Args:
            files: List of all file paths

        Returns:
            List of created index paths
        """
        if not self.per_directory:
            return []

        # Group files by directory
        dir_files: dict[Path, list[Path]] = defaultdict(list)

        for file_path in files:
            dir_files[file_path.parent].append(file_path)

        created_indexes = []

        for directory, dir_file_list in dir_files.items():
            if len(dir_file_list) <= 1:
                continue  # Skip directories with single file

            index_path = directory / "INDEX.md"

            try:
                rel_dir = directory.relative_to(self.output_dir)
                title = f"{rel_dir} - Index"
            except ValueError:
                title = f"{directory.name} - Index"

            content = self.generate_index(dir_file_list, title=title)

            with open(index_path, "w", encoding="utf-8") as f:
                f.write(content)

            created_indexes.append(index_path)
            logger.debug(f"Created directory index at {index_path}")

        return created_indexes

    def create_all_indexes(self, files: list[Path]) -> IndexResult:
        """Create all configured indexes.

        Args:
            files: List of file paths to index

        Returns:
            Dict with created indexes and stats
        """
        # Create main index
        main_index_path = self.create_index(files)

        # Create directory indexes
        dir_indexes: list[Path] = []
        if self.per_directory:
            dir_indexes = self.create_directory_indexes(files)

        result: IndexResult = {
            "main_index": main_index_path,
            "directory_indexes": dir_indexes,
            "files_indexed": len(files),
        }

        logger.info(
            f"Created {1 + len(result['directory_indexes'])} index files " f"for {len(files)} documents"
        )

        return result
