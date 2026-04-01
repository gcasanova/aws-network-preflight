"""Typer CLI entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from botocore.exceptions import BotoCoreError, ClientError

from preflight.auth import AccountIdentityError, SessionFactory
from preflight.config import STARTER_CONFIG_YAML, PreflightConfigError, load_config
from preflight.discovery import SelectorResolutionError, resolve_assertion_targets
from preflight.exit_codes import ExitCode
from preflight.models import PreflightConfig
from preflight.reporters.console import (
    build_console,
    print_assertion_explanation,
    print_created_files,
    print_resolved_targets,
    print_run_summary,
    print_validation_success,
)
from preflight.runner import AssertionNotFoundError, run_assertion, run_assertions

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

    target_paths = [file, examples_dir / "preflight.yaml"]
    _validate_writable_paths(target_paths, force=force)
    created_files = [_write_file(path, STARTER_CONFIG_YAML) for path in target_paths]
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

    config = _load_config_or_exit(file)
    cli_context = _cli_context(ctx)
    session_factory = SessionFactory(
        config.defaults,
        config.accounts,
        profile_override=cli_context.profile_override,
    )

    try:
        resolved_targets = resolve_assertion_targets(config, session_factory=session_factory)
    except (SelectorResolutionError, AccountIdentityError, BotoCoreError, ClientError) as exc:
        _exit_runtime_error(str(exc))

    print_resolved_targets(console, resolved_targets)


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

    config = _load_config_or_exit(file)
    cli_context = _cli_context(ctx)

    try:
        summary = run_assertions(config, profile_override=cli_context.profile_override)
    except (SelectorResolutionError, AccountIdentityError, BotoCoreError, ClientError) as exc:
        _exit_runtime_error(str(exc))

    print_run_summary(console, summary)

    if summary.error_count:
        raise typer.Exit(code=int(ExitCode.RUNTIME_ERROR))
    if summary.failed_count:
        raise typer.Exit(code=int(ExitCode.ASSERTION_FAILED))


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
    cli_context = _cli_context(ctx)

    try:
        result = run_assertion(config, assertion_id, profile_override=cli_context.profile_override)
    except AssertionNotFoundError as exc:
        _exit_config_error(str(exc))
    except (SelectorResolutionError, AccountIdentityError, BotoCoreError, ClientError) as exc:
        _exit_runtime_error(str(exc))

    print_assertion_explanation(console, result)

    if result.status == "error":
        raise typer.Exit(code=int(ExitCode.RUNTIME_ERROR))
    if result.status == "failed":
        raise typer.Exit(code=int(ExitCode.ASSERTION_FAILED))


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


def _validate_writable_paths(paths: list[Path], force: bool) -> None:
    """Fail fast before writing if any target path would be rejected."""

    for path in paths:
        if path.exists() and path.is_dir():
            _exit_config_error(f"Expected a file path but found a directory: {path}")

        for ancestor in [path.parent, *path.parent.parents]:
            if ancestor == ancestor.parent:
                continue
            if ancestor.exists() and not ancestor.is_dir():
                if ancestor == path.parent and path.name == "preflight.yaml":
                    _exit_config_error(f"Expected a directory path but found a file: {ancestor}")
                _exit_config_error(
                    f"Cannot create parent directory for {path}: {ancestor} is a file"
                )

        if path.exists() and not force:
            _exit_config_error(f"Refusing to overwrite existing file without --force: {path}")


def _write_file(path: Path, contents: str) -> Path:
    """Write a file, creating parent directories as needed."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    except OSError as exc:
        _exit_config_error(f"Failed to write {path}: {exc}")
    return path


def _exit_config_error(message: str) -> NoReturn:
    """Exit with a formatted config error."""

    console.print(f"[red]Config error:[/red] {message}")
    raise typer.Exit(code=int(ExitCode.CONFIG_ERROR))


def _exit_runtime_error(message: str) -> NoReturn:
    """Exit with a formatted runtime error."""

    console.print(f"[red]Runtime error:[/red] {message}")
    raise typer.Exit(code=int(ExitCode.RUNTIME_ERROR))


if __name__ == "__main__":
    app()
