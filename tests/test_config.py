from __future__ import annotations

from pathlib import Path

import pytest

from preflight.config import STARTER_CONFIG_YAML, PreflightConfigError, load_config


def test_load_config_accepts_valid_example(tmp_path: Path) -> None:
    config_path = tmp_path / "preflight.yaml"
    config_path.write_text(STARTER_CONFIG_YAML, encoding="utf-8")

    config = load_config(config_path)

    assert config.version == 1
    assert len(config.accounts) == 2
    assert len(config.assertions) == 2


def test_load_config_rejects_selector_with_multiple_matchers(tmp_path: Path) -> None:
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
  - id: invalid-selector
    type: allow
    source:
      account: app
      selector:
        resource_id: i-1234567890abcdef0
        arn: arn:aws:ec2:us-east-1:111111111111:instance/i-1234567890abcdef0
    destination:
      account: app
      selector:
        tags:
          Name: db
    protocol: tcp
    port: 443
""",
        encoding="utf-8",
    )

    with pytest.raises(PreflightConfigError, match="exactly one of resource_id, arn, or tags"):
        load_config(config_path)


def test_load_config_rejects_unknown_account_reference(tmp_path: Path) -> None:
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
  - id: unknown-account
    type: deny
    source:
      account: missing
      selector:
        resource_id: i-1234567890abcdef0
    destination:
      account: app
      selector:
        resource_id: i-abcdef12345678900
    protocol: tcp
    port: 5432
""",
        encoding="utf-8",
    )

    with pytest.raises(PreflightConfigError, match="references unknown account 'missing'"):
        load_config(config_path)
