from __future__ import annotations

import tomllib
from pathlib import Path


def test_console_scripts_include_short_alias() -> None:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as file_handle:
        pyproject = tomllib.load(file_handle)

    scripts = pyproject["project"]["scripts"]

    assert scripts["aws-network-preflight"] == "preflight.cli:app"
    assert scripts["anp"] == "preflight.cli:app"
