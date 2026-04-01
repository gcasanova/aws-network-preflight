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
