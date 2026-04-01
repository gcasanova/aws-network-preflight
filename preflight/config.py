"""Config loading and starter templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from preflight.models import PreflightConfig

STARTER_CONFIG_YAML = """\
version: 1

defaults:
  region: us-east-1
  auth:
    mode: profile
    profile: default

accounts:
  shared:
    role_arn: arn:aws:iam::111111111111:role/PreflightReadRole
    regions: [us-east-1]

  app:
    role_arn: arn:aws:iam::222222222222:role/PreflightReadRole
    regions: [us-east-1]

assertions:
  - id: dev-to-shared-dns-allow
    type: allow
    source:
      account: app
      selector:
        tags:
          Name: app-dev-ec2
    destination:
      account: shared
      selector:
        tags:
          Name: shared-dns-endpoint
    protocol: tcp
    port: 53

  - id: dev-to-prod-db-deny
    type: deny
    source:
      account: app
      selector:
        tags:
          Name: app-dev-ec2
    destination:
      account: app
      selector:
        tags:
          Name: app-prod-db
    protocol: tcp
    port: 5432
"""


class PreflightConfigError(ValueError):
    """Raised when the config file cannot be loaded or validated."""


def load_yaml_document(path: Path) -> dict[str, Any]:
    """Load a YAML document and ensure the root value is a mapping."""

    if not path.exists():
        raise PreflightConfigError(f"Config file not found: {path}")

    try:
        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PreflightConfigError(f"Invalid YAML in {path}: {exc}") from exc

    if raw_document is None:
        raise PreflightConfigError(f"Config file is empty: {path}")

    if not isinstance(raw_document, dict):
        raise PreflightConfigError(f"Config root must be a YAML mapping/object: {path}")

    return raw_document


def load_config(path: Path) -> PreflightConfig:
    """Load and validate a preflight config file."""

    raw_document = load_yaml_document(path)

    try:
        return PreflightConfig.model_validate(raw_document)
    except ValidationError as exc:
        raise PreflightConfigError(format_validation_error(exc)) from exc


def format_validation_error(exc: ValidationError) -> str:
    """Render Pydantic errors as readable CLI output."""

    lines: list[str] = []

    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "config"
        lines.append(f"- {location}: {error['msg']}")

    return "\n".join(lines)
