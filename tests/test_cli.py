from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from preflight.cli import app
from preflight.config import STARTER_CONFIG_YAML
from preflight.exit_codes import ExitCode

runner = CliRunner()


def test_validate_command_succeeds(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(STARTER_CONFIG_YAML, encoding="utf-8")

    result = runner.invoke(app, ["validate", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.OK)
    assert "Validated" in result.stdout


def test_init_command_creates_config_and_example_files(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    examples_dir = tmp_path / "examples" / "basic"

    result = runner.invoke(
        app,
        [
            "init",
            "-f",
            str(config_path),
            "--examples-dir",
            str(examples_dir),
        ],
    )

    assert result.exit_code == int(ExitCode.OK)
    assert config_path.exists()
    assert (examples_dir / "preflight.yaml").exists()


def test_init_command_refuses_partial_write_when_one_target_exists(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    examples_dir = tmp_path / "examples" / "basic"
    existing_example_path = examples_dir / "preflight.yaml"
    existing_example_path.parent.mkdir(parents=True, exist_ok=True)
    existing_example_path.write_text("existing contents\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "init",
            "-f",
            str(config_path),
            "--examples-dir",
            str(examples_dir),
        ],
    )

    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "Refusing to overwrite existing file without --force" in result.stdout
    assert not config_path.exists()
    assert existing_example_path.read_text(encoding="utf-8") == "existing contents\n"


def test_explain_command_returns_clean_error_for_unknown_assertion_id(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(STARTER_CONFIG_YAML, encoding="utf-8")

    result = runner.invoke(app, ["explain", "-f", str(config_path), "--id", "missing-id"])

    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "Config error: Assertion 'missing-id' was not found in the config" in result.stdout
