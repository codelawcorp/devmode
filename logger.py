import logging
import sys
import os
import datetime

# ANSI escape codes for colors
COLORS = {
    "GREY": "\033[38;5;240m",  # Grey for DEBUG
    "GREEN": "\033[32m",  # Not used
    "WHITE": "\033[97m",  # Bright White for INFO
    "YELLOW": "\033[33m",  # Yellow for WARNING
    "RED": "\033[31m",  # Red for ERROR
    "BOLD_RED": "\033[31;1m",  # Bold Red for CRITICAL
    "RESET": "\033[0m",  # Reset color
}


class CustomFormatter(logging.Formatter):
    """
    Custom formatter to output logs in a simple text format with colors.
    Shows context information only in DEBUG mode.
    """

    LEVEL_COLORS = {
        logging.DEBUG: COLORS["GREY"],
        logging.INFO: COLORS["WHITE"],
        logging.WARNING: COLORS["YELLOW"],
        logging.ERROR: COLORS["RED"],
        logging.CRITICAL: COLORS["BOLD_RED"],
    }

    def format(self, record):
        # Use the built-in formatting to get asctime, then manually construct the log record
        super().format(record)
        timestamp = datetime.datetime.utcfromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        message = f"{record.getMessage()}"

        # Get color for current level
        color = self.LEVEL_COLORS.get(record.levelno, COLORS["RESET"])

        # Only include context in DEBUG level
        if record.levelno == logging.DEBUG:
            context = f"(Process ID: {record.process}, File: {record.pathname}, Line: {record.lineno})"
            log_record = f"{color}{timestamp} {record.levelname} {context} {message}{COLORS['RESET']}"
        else:
            log_record = (
                f"{color}{timestamp} {record.levelname} {message}{COLORS['RESET']}"
            )

        if record.exc_info:
            log_record += f"\n{color}Exception: {self.formatException(record.exc_info)}{COLORS['RESET']}"
        return log_record


def setup_root_logger(level=logging.INFO):
    """
    Set up the root logger with the specified level.
    """
    root_logger = logging.getLogger()

    # Remove default handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Convert string level to logging level constant
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root_logger.setLevel(level)

    # Create a logging handler that outputs log messages to stdout
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(CustomFormatter())

    # Add the handler to the root_logger
    root_logger.addHandler(handler)

    return root_logger


logger = setup_root_logger(os.environ.get("LOG_LEVEL", "INFO"))
