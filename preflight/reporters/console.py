"""Console reporting helpers."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from preflight.discovery import ResolvedAssertionTarget, ResolvedTarget
from preflight.models import PreflightConfig
from preflight.runner import AssertionResult, RunSummary, humanize_outcome


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


def print_run_summary(console: Console, summary: RunSummary) -> None:
    """Render run results for all assertions."""

    table = Table(title="Assertion Results")
    table.add_column("Assertion ID", no_wrap=True)
    table.add_column("Expected", no_wrap=True)
    table.add_column("Actual", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Analysis ID", no_wrap=True)
    table.add_column("Detail")

    for result in summary.results:
        table.add_row(
            result.assertion_id,
            humanize_outcome(result.expected_outcome),
            humanize_outcome(result.actual_outcome),
            _render_status(result.status),
            result.analysis_id or "-",
            result.message,
        )

    console.print(table)
    console.print(
        f"Passed: {summary.passed_count}  "
        f"Failed: {summary.failed_count}  "
        f"Errors: {summary.error_count}"
    )


def print_assertion_explanation(console: Console, result: AssertionResult) -> None:
    """Render detailed output for one assertion execution."""

    target_table = Table(title=f"Assertion {result.assertion_id}")
    target_table.add_column("Field", no_wrap=True)
    target_table.add_column("Value")
    target_table.add_row("Type", result.assertion_type)
    target_table.add_row("Expected", humanize_outcome(result.expected_outcome))
    target_table.add_row("Actual", humanize_outcome(result.actual_outcome))
    target_table.add_row("Status", _render_status(result.status))
    target_table.add_row("Source", _format_resolved_target(result.source))
    target_table.add_row("Destination", _format_resolved_target(result.destination))
    target_table.add_row("Path ID", result.path_id or "-")
    target_table.add_row("Analysis ID", result.analysis_id or "-")
    target_table.add_row("Analysis Status", result.analysis_status or "-")
    target_table.add_row("Message", result.message)

    if result.network_path_found is not None:
        target_table.add_row("Network Path Found", "yes" if result.network_path_found else "no")
    if result.status_message:
        target_table.add_row("Status Message", result.status_message)
    if result.explanation_code:
        target_table.add_row("Explanation Code", result.explanation_code)
    if result.explanation_summary:
        target_table.add_row("Explanation", result.explanation_summary)
    if result.cleanup.errors:
        target_table.add_row("Cleanup Errors", "; ".join(result.cleanup.errors))

    console.print(target_table)


def _render_status(status: str) -> str:
    """Return styled status text for console output."""

    if status == "passed":
        return "[green]passed[/green]"
    if status == "failed":
        return "[red]failed[/red]"
    return "[yellow]error[/yellow]"


def _format_resolved_target(target: ResolvedTarget) -> str:
    """Render a resolved target compactly for explain output."""

    if target.normalized:
        return (
            f"{target.account}/{target.region} "
            f"{target.input_type} {target.input_identifier} -> "
            f"{target.resolved_target_type} {target.resolved_identifier}"
        )
    return (
        f"{target.account}/{target.region} "
        f"{target.resolved_target_type} {target.resolved_identifier}"
    )
