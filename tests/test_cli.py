from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from preflight.cli import app
from preflight.config import STARTER_CONFIG_YAML
from preflight.exit_codes import ExitCode
from tests.fakes import FakeEC2Client, FakeSessionFactory, make_eni, make_instance

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


def test_init_command_fails_when_examples_dir_path_is_a_file(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    examples_dir = tmp_path / "examples-basic"
    examples_dir.write_text("not a directory\n", encoding="utf-8")

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
    assert "Expected a directory path but found a file" in result.stdout
    assert not config_path.exists()


def test_init_command_fails_when_parent_path_is_a_file(tmp_path: Path) -> None:
    blocking_parent = tmp_path / "blocked"
    blocking_parent.write_text("not a directory\n", encoding="utf-8")
    config_path = blocking_parent / "preflight.yaml"

    result = runner.invoke(app, ["init", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "Expected a directory path but found a file" in result.stdout


def test_explain_command_returns_clean_error_for_unknown_assertion_id(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(STARTER_CONFIG_YAML, encoding="utf-8")

    result = runner.invoke(app, ["explain", "-f", str(config_path), "--id", "missing-id"])

    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "Config error: Assertion 'missing-id' was not found in the config" in result.stdout


def test_list_targets_command_prints_resolved_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(
        """\
version: 1
defaults:
  region: us-east-1
accounts:
  app:
    regions: [us-east-1]
assertions:
  - id: list-targets-success
    type: allow
    source:
      account: app
      selector:
        resource_id: i-0123456789abcdef0
    destination:
      account: app
      selector:
        resource_id: eni-0fedcba9876543210
    protocol: tcp
    port: 443
""",
        encoding="utf-8",
    )
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                enis_by_id={
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                },
                instances_by_id={
                    "i-0123456789abcdef0": make_instance(
                        "i-0123456789abcdef0",
                        primary_eni_id="eni-0123456789abcdef0",
                    )
                },
            )
        }
    )
    monkeypatch.setattr("preflight.cli.SessionFactory", lambda *args, **kwargs: session_factory)

    result = runner.invoke(app, ["list-targets", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.OK)
    assert "Resolved Targets" in result.stdout
    assert "list-targets-success" in result.stdout
    assert "ec2_instance -> eni" in result.stdout
    assert "eni-0123456789abcdef0" in result.stdout


def test_list_targets_command_returns_runtime_error_on_resolution_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(
        """\
version: 1
defaults:
  region: us-east-1
accounts:
  app:
    regions: [us-east-1]
assertions:
  - id: list-targets-failure
    type: allow
    source:
      account: app
      selector:
        tags:
          Name: ambiguous
    destination:
      account: app
      selector:
        resource_id: eni-0fedcba9876543210
    protocol: tcp
    port: 443
""",
        encoding="utf-8",
    )
    session_factory = FakeSessionFactory(
        {
            "app": FakeEC2Client(
                enis_by_id={
                    "eni-0fedcba9876543210": make_eni("eni-0fedcba9876543210"),
                },
                tag_enis=[make_eni("eni-0123456789abcdef0")],
                tag_instances=[
                    make_instance(
                        "i-0123456789abcdef0",
                        primary_eni_id="eni-11111111111111111",
                    )
                ],
            )
        }
    )
    monkeypatch.setattr("preflight.cli.SessionFactory", lambda *args, **kwargs: session_factory)

    result = runner.invoke(app, ["list-targets", "-f", str(config_path)])

    assert result.exit_code == int(ExitCode.RUNTIME_ERROR)
    assert "Runtime error:" in result.stdout
    assert "matched multiple supported v1 targets" in result.stdout
