"""Console reporting helpers."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from preflight.discovery import ResolvedAssertionTarget
from preflight.models import PreflightConfig


def build_console() -> Console:
    """Create the shared CLI console."""

    return Console(highlight=False, width=160)


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


def print_resolved_targets(
    console: Console,
    resolved_targets: list[ResolvedAssertionTarget],
) -> None:
    """Render resolved assertion endpoints."""

    table = Table(title="Resolved Targets")
    table.add_column("Assertion ID", no_wrap=True)
    table.add_column("Endpoint", no_wrap=True)
    table.add_column("Account", no_wrap=True)
    table.add_column("Region", no_wrap=True)
    table.add_column("Selector", no_wrap=True)
    table.add_column("Resolved Type", no_wrap=True)
    table.add_column("Resolved Identifier", no_wrap=True)
    table.add_column("Normalized", no_wrap=True)

    for item in resolved_targets:
        target = item.target
        normalized = "no"
        if target.normalized:
            normalized = f"{target.input_type} -> {target.resolved_target_type}"

        table.add_row(
            item.assertion_id,
            item.endpoint_role,
            target.account,
            target.region,
            target.selector_type,
            target.resolved_target_type,
            target.resolved_identifier,
            normalized,
        )

    console.print(table)
