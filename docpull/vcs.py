"""Version control system integration (Git)."""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class GitIntegration:
    """Git integration for tracking documentation changes."""

    def __init__(self, repo_dir: Path):
        """Initialize Git integration.

        Args:
            repo_dir: Directory containing .git folder
        """
        self.repo_dir = Path(repo_dir)

        if not self._is_git_repo():
            logger.warning(f"{repo_dir} is not a Git repository")

    def _is_git_repo(self) -> bool:
        """Check if directory is a Git repository.

        Returns:
            True if .git exists
        """
        return (self.repo_dir / ".git").exists()

    def _run_git(self, *args) -> tuple[bool, str]:
        """Run a git command.

        Args:
            *args: Git command arguments

        Returns:
            Tuple of (success, output)
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_dir)] + list(args), capture_output=True, text=True, timeout=30
            )

            success = result.returncode == 0
            output = result.stdout if success else result.stderr

            return success, output.strip()

        except subprocess.TimeoutExpired:
            logger.error("Git command timed out")
            return False, "Command timed out"
        except Exception as e:
            logger.error(f"Git command failed: {e}")
            return False, str(e)

    def get_status(self) -> Optional[str]:
        """Get git status.

        Returns:
            Status output or None if failed
        """
        success, output = self._run_git("status", "--short")
        return output if success else None

    def has_changes(self) -> bool:
        """Check if there are uncommitted changes.

        Returns:
            True if there are changes
        """
        status = self.get_status()
        return bool(status) if status is not None else False

    def add_files(self, patterns: Optional[list[str]] = None) -> bool:
        """Add files to staging area.

        Args:
            patterns: File patterns to add (default: ['.'])

        Returns:
            True if successful
        """
        patterns = patterns or ["."]

        for pattern in patterns:
            success, output = self._run_git("add", pattern)
            if not success:
                logger.error(f"Failed to add {pattern}: {output}")
                return False

        logger.info(f"Added files: {', '.join(patterns)}")
        return True

    def commit(self, message: str, author: Optional[str] = None) -> bool:
        """Create a commit.

        Args:
            message: Commit message
            author: Optional author string (e.g., "Name <email>")

        Returns:
            True if successful
        """
        if not self.has_changes():
            logger.info("No changes to commit")
            return True

        args = ["commit", "-m", message]

        if author:
            args.extend(["--author", author])

        success, output = self._run_git(*args)

        if success:
            logger.info(f"Created commit: {message}")
        else:
            logger.error(f"Failed to commit: {output}")

        return success

    def tag(self, tag_name: str, message: Optional[str] = None) -> bool:
        """Create a tag.

        Args:
            tag_name: Tag name
            message: Optional tag message

        Returns:
            True if successful
        """
        args = ["tag"]

        if message:
            args.extend(["-a", tag_name, "-m", message])
        else:
            args.append(tag_name)

        success, output = self._run_git(*args)

        if success:
            logger.info(f"Created tag: {tag_name}")
        else:
            logger.error(f"Failed to create tag: {output}")

        return success

    def auto_commit(
        self,
        message_template: str = "Update docs - {date}",
        patterns: Optional[list[str]] = None,
        author: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> bool:
        """Automatically add, commit, and optionally tag changes.

        Args:
            message_template: Commit message template ({date} will be replaced)
            patterns: File patterns to add
            author: Optional author
            tag: Optional tag name

        Returns:
            True if successful
        """
        if not self._is_git_repo():
            logger.error("Not a Git repository")
            return False

        if not self.has_changes():
            logger.info("No changes to commit")
            return True

        # Add files
        if not self.add_files(patterns):
            return False

        # Create commit message
        message = message_template.format(
            date=datetime.now().strftime("%Y-%m-%d"),
            datetime=datetime.now().isoformat(),
        )

        # Commit
        if not self.commit(message, author):
            return False

        # Tag if requested
        if tag:
            self.tag(tag, f"Docs snapshot: {message}")

        return True

    def get_diff(self, cached: bool = False) -> Optional[str]:
        """Get git diff.

        Args:
            cached: Show staged changes

        Returns:
            Diff output or None
        """
        args = ["diff"]
        if cached:
            args.append("--cached")

        success, output = self._run_git(*args)
        return output if success else None

    def get_log(self, max_count: int = 10) -> Optional[str]:
        """Get commit log.

        Args:
            max_count: Maximum number of commits

        Returns:
            Log output or None
        """
        success, output = self._run_git("log", f"--max-count={max_count}", "--oneline")
        return output if success else None
