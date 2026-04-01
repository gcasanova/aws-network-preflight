"""Typer CLI entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from preflight.config import STARTER_CONFIG_YAML, PreflightConfigError, load_config
from preflight.exit_codes import ExitCode
from preflight.models import PreflightConfig
from preflight.reporters.console import (
    build_console,
    print_created_files,
    print_not_implemented,
    print_validation_success,
)
from preflight.runner import find_assertion

app = typer.Typer(
    add_completion=False,
    help="Declare your AWS network intent in YAML and verify that connectivity still matches it.",
    no_args_is_help=True,
)


@dataclass(slots=True)
class CLIContext:
    """Global CLI options shared across commands."""

    profile_override: str | None = None


console = build_console()


@app.callback()
def main(
    ctx: typer.Context,
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            help="Override the AWS profile for commands that call AWS APIs.",
        ),
    ] = None,
) -> None:
    """Configure shared CLI context."""

    ctx.obj = CLIContext(profile_override=profile)


@app.command()
def init(
    file: Annotated[
        Path,
        typer.Option(
            "--file",
            "-f",
            help="Path to write the starter config to.",
        ),
    ] = Path("preflight.yaml"),
    examples_dir: Annotated[
        Path,
        typer.Option(
            "--examples-dir",
            help="Directory to write the example config into.",
        ),
    ] = Path("examples/basic"),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Overwrite files if they already exist.",
        ),
    ] = False,
) -> None:
    """Create a starter config and example directory."""

    created_files = [
        _write_file(file, STARTER_CONFIG_YAML, force=force),
        _write_file(examples_dir / "preflight.yaml", STARTER_CONFIG_YAML, force=force),
    ]
    print_created_files(console, created_files)


@app.command()
def validate(
    file: Annotated[
        Path,
        typer.Option(
            "--file",
            "-f",
            help="Path to the YAML config file.",
        ),
    ] = Path("preflight.yaml"),
) -> None:
    """Validate config structure without calling AWS APIs."""

    config = _load_config_or_exit(file)
    print_validation_success(console, file, config)


@app.command("list-targets")
def list_targets(
    ctx: typer.Context,
    file: Annotated[
        Path,
        typer.Option(
            "--file",
            "-f",
            help="Path to the YAML config file.",
        ),
    ] = Path("preflight.yaml"),
) -> None:
    """Resolve selectors without running assertions."""

    _load_config_or_exit(file)
    _ = _cli_context(ctx)
    _exit_not_implemented(
        "list-targets",
        "Selector resolution is planned for the next phase.",
    )


@app.command()
def run(
    ctx: typer.Context,
    file: Annotated[
        Path,
        typer.Option(
            "--file",
            "-f",
            help="Path to the YAML config file.",
        ),
    ] = Path("preflight.yaml"),
) -> None:
    """Run all assertions and fail if any assertion fails."""

    _load_config_or_exit(file)
    _ = _cli_context(ctx)
    _exit_not_implemented(
        "run",
        "Reachability Analyzer execution is planned for the next phase.",
    )


@app.command()
def explain(
    ctx: typer.Context,
    assertion_id: str = typer.Option(
        ...,
        "--id",
        help="ID of the assertion to explain.",
    ),
    file: Annotated[
        Path,
        typer.Option(
            "--file",
            "-f",
            help="Path to the YAML config file.",
        ),
    ] = Path("preflight.yaml"),
) -> None:
    """Explain one assertion in detail."""

    config = _load_config_or_exit(file)
    _ = _cli_context(ctx)

    try:
        find_assertion(config, assertion_id)
    except KeyError as exc:
        _exit_config_error(str(exc))

    _exit_not_implemented(
        "explain",
        "Detailed Reachability Analyzer-backed explain output is planned for the next phase.",
    )


def _cli_context(ctx: typer.Context) -> CLIContext:
    """Return typed CLI context."""

    if isinstance(ctx.obj, CLIContext):
        return ctx.obj

    cli_context = CLIContext()
    ctx.obj = cli_context
    return cli_context


def _load_config_or_exit(path: Path) -> PreflightConfig:
    """Load config or exit with the config error code."""

    try:
        return load_config(path)
    except PreflightConfigError as exc:
        _exit_config_error(str(exc))


def _write_file(path: Path, contents: str, force: bool) -> Path:
    """Write a file, creating parent directories as needed."""

    if path.exists() and not force:
        _exit_config_error(f"Refusing to overwrite existing file without --force: {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def _exit_config_error(message: str) -> NoReturn:
    """Exit with a formatted config error."""

    console.print(f"[red]Config error:[/red] {message}")
    raise typer.Exit(code=int(ExitCode.CONFIG_ERROR))


def _exit_not_implemented(command_name: str, detail: str) -> NoReturn:
    """Exit with a runtime code for scaffolded commands."""

    print_not_implemented(console, command_name, detail)
    raise typer.Exit(code=int(ExitCode.RUNTIME_ERROR))


if __name__ == "__main__":
    app()
