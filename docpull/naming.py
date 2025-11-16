"""Smart naming strategies for output files."""

import hashlib
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


class NamingStrategy:
    """Base class for file naming strategies."""

    @staticmethod
    def sanitize(name: str) -> str:
        """Sanitize a name for use in filenames.

        Args:
            name: Name to sanitize

        Returns:
            Sanitized name
        """
        # Remove or replace invalid characters
        name = re.sub(r'[<>:"|?*]', "", name)
        name = re.sub(r"[\\\/]", "-", name)
        name = re.sub(r"\s+", "-", name)
        name = re.sub(r"-+", "-", name)
        name = name.strip("-")

        # Limit length
        if len(name) > 200:
            # Hash the overflow to prevent collisions
            overflow = name[180:]
            name_hash = hashlib.md5(overflow.encode()).hexdigest()[:8]
            name = name[:180] + "-" + name_hash

        return name or "index"


class FullNamingStrategy(NamingStrategy):
    """Full path naming (current default behavior).

    Preserves complete path structure from URL.
    Example: docs.example.com/en/api/reference.html -> docs_example_com/en_api_reference.md
    """

    def generate_path(
        self, url: str, base_url: Optional[str] = None, output_dir: Path = Path("./docs")
    ) -> Path:
        """Generate file path from URL.

        Args:
            url: Source URL
            base_url: Base URL to strip (optional)
            output_dir: Output directory

        Returns:
            Full file path
        """
        parsed = urlparse(url)

        # Create domain-based subdirectory
        domain = parsed.netloc.replace(".", "_").replace(":", "_")

        # Convert path to filename
        path = parsed.path.strip("/")

        if not path:
            path = "index"

        # Remove base URL if provided
        if base_url:
            base_parsed = urlparse(base_url)
            base_path = base_parsed.path.strip("/")
            if path.startswith(base_path):
                path = path[len(base_path) :].strip("/")

        # Replace slashes with underscores
        filename = path.replace("/", "_")
        filename = self.sanitize(filename)

        # Remove common extensions
        filename = re.sub(r"\.(html?|php|aspx?)$", "", filename, flags=re.IGNORECASE)

        return output_dir / domain / f"{filename}.md"


class ShortNamingStrategy(NamingStrategy):
    """Short naming strategy.

    Removes domain prefix but keeps directory structure.
    Example: docs.example.com/en/api/reference.html -> api/reference.md
    """

    def generate_path(
        self, url: str, base_url: Optional[str] = None, output_dir: Path = Path("./docs")
    ) -> Path:
        """Generate short file path from URL.

        Args:
            url: Source URL
            base_url: Base URL to strip
            output_dir: Output directory

        Returns:
            Short file path
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")

        if not path:
            return output_dir / "index.md"

        # Remove base URL
        if base_url:
            base_parsed = urlparse(base_url)
            base_path = base_parsed.path.strip("/")
            if path.startswith(base_path):
                path = path[len(base_path) :].strip("/")

        # Remove file extension
        path = re.sub(r"\.(html?|php|aspx?)$", "", path, flags=re.IGNORECASE)

        # Sanitize path components
        parts = [self.sanitize(p) for p in path.split("/")]

        return output_dir / Path(*parts[:-1]) / f"{parts[-1]}.md"


class FlatNamingStrategy(NamingStrategy):
    """Flat naming strategy.

    All files in single directory with descriptive names.
    Example: docs.example.com/en/api/reference.html -> api-reference.md
    """

    def generate_path(
        self, url: str, base_url: Optional[str] = None, output_dir: Path = Path("./docs")
    ) -> Path:
        """Generate flat file path from URL.

        Args:
            url: Source URL
            base_url: Base URL to strip
            output_dir: Output directory

        Returns:
            Flat file path
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")

        if not path:
            return output_dir / "index.md"

        # Remove base URL
        if base_url:
            base_parsed = urlparse(base_url)
            base_path = base_parsed.path.strip("/")
            if path.startswith(base_path):
                path = path[len(base_path) :].strip("/")

        # Remove file extension
        path = re.sub(r"\.(html?|php|aspx?)$", "", path, flags=re.IGNORECASE)

        # Convert slashes to hyphens
        filename = path.replace("/", "-")
        filename = self.sanitize(filename)

        return output_dir / f"{filename}.md"


class HierarchicalNamingStrategy(NamingStrategy):
    """Hierarchical naming strategy.

    Smart hierarchy based on common documentation patterns.
    Example: docs.example.com/en/api/reference.html -> api/reference.md
              docs.example.com/guides/intro.html -> guides/intro.md
    """

    # Common doc path prefixes to remove
    REMOVE_PREFIXES = ["docs", "documentation", "en", "english", "v1", "v2", "latest", "stable"]

    def generate_path(
        self, url: str, base_url: Optional[str] = None, output_dir: Path = Path("./docs")
    ) -> Path:
        """Generate hierarchical file path from URL.

        Args:
            url: Source URL
            base_url: Base URL to strip
            output_dir: Output directory

        Returns:
            Hierarchical file path
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")

        if not path:
            return output_dir / "index.md"

        # Remove base URL
        if base_url:
            base_parsed = urlparse(base_url)
            base_path = base_parsed.path.strip("/")
            if path.startswith(base_path):
                path = path[len(base_path) :].strip("/")

        # Remove file extension
        path = re.sub(r"\.(html?|php|aspx?)$", "", path, flags=re.IGNORECASE)

        # Split into parts
        parts = [p for p in path.split("/") if p]

        # Remove common prefixes
        while parts and parts[0].lower() in self.REMOVE_PREFIXES:
            parts.pop(0)

        if not parts:
            parts = ["index"]

        # Sanitize parts
        parts = [self.sanitize(p) for p in parts]

        # Build path
        if len(parts) == 1:
            return output_dir / f"{parts[0]}.md"
        else:
            return output_dir / Path(*parts[:-1]) / f"{parts[-1]}.md"


def get_naming_strategy(strategy_name: str) -> NamingStrategy:
    """Get naming strategy by name.

    Args:
        strategy_name: Strategy name ('full', 'short', 'flat', 'hierarchical')

    Returns:
        Naming strategy instance

    Raises:
        ValueError: If strategy name is unknown
    """
    strategies = {
        "full": FullNamingStrategy,
        "short": ShortNamingStrategy,
        "flat": FlatNamingStrategy,
        "hierarchical": HierarchicalNamingStrategy,
    }

    strategy_class = strategies.get(strategy_name.lower())
    if not strategy_class:
        raise ValueError(
            f"Unknown naming strategy: {strategy_name}. "
            f"Available strategies: {', '.join(strategies.keys())}"
        )

    return strategy_class()
