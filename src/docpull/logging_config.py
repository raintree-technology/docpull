import logging
import sys
from typing import Optional


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    format_string: Optional[str] = None,
    force: bool = False,
) -> logging.Logger:
    """
    Set up logging configuration for docpull.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging output
        format_string: Optional custom format string for log messages
        force: If True, reconfigure even if handlers exist

    Returns:
        Configured logger instance

    Raises:
        AttributeError: If invalid logging level is provided
    """
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("docpull")
    logger.setLevel(numeric_level)

    # Only clear and reconfigure if forced or no handlers exist
    if force or not logger.handlers:
        logger.handlers.clear()

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_formatter = logging.Formatter(format_string)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(numeric_level)
            file_formatter = logging.Formatter(format_string)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

    # Prevent propagation to root logger to avoid duplicate logs
    logger.propagate = False

    return logger
