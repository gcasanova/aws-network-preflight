"""Stable process exit codes for the CLI."""

from enum import IntEnum


class ExitCode(IntEnum):
    """CLI exit codes."""

    OK = 0
    ASSERTION_FAILED = 1
    CONFIG_ERROR = 2
    RUNTIME_ERROR = 3
