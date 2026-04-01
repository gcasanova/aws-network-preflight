"""Console reporting helpers."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from preflight.models import PreflightConfig


def build_console() -> Console:
    """Create the shared CLI console."""

    return Console(highlight=False)


def print_created_files(console: Console, created_files: list[Path]) -> None:
    """Render a small table of generated files."""

    table = Table(title="Initialized aws-network-preflight")
    table.add_column("Created")

    for path in created_files:
        table.add_row(str(path))

    console.print(table)


def print_validation_success(console: Console, path: Path, config: PreflightConfig) -> None:
    """Render a concise validation success message."""

    console.print(
        "[green]Validated[/green] "
        f"{path} "
        f"({len(config.accounts)} accounts, {len(config.assertions)} assertions)"
    )


def print_not_implemented(console: Console, command_name: str, detail: str) -> None:
    """Render a transparent scaffold notice."""

    console.print(f"[yellow]{command_name} is scaffolded but not implemented yet.[/yellow]")
    console.print(detail)
