# AGENTS.md

## Priorities

- Keep the project scope intentionally narrow.
- Do not add new AWS services, analysis types, or broader platform features unless explicitly requested.
- Preserve the current v1 boundaries:
  - single-region-only
  - Reachability Analyzer only
  - ENI is the canonical execution target
  - EC2 instance is convenience input
  - standard commercial AWS partition only

## Required validation

For Python/code changes, do not consider the task complete until you have run:

    pip install -e ".[dev]"
    ruff check .
    ruff format --check .
    mypy preflight
    pytest

If `ruff format --check .` fails, run:

    ruff format .

Then rerun:

    ruff check .
    ruff format --check .
    mypy preflight
    pytest

If you changed docs only, code checks are not required unless the task also changed code or config that affects them.

## Docs accuracy

If behavior, CLI usage, or repo scope changes, update `README.md` and `docs/plan.md`.
Do not claim features that are not implemented.

## Done criteria

A task is done only when all of the following are true:

- the requested change is implemented
- scope boundaries were preserved unless explicitly changed
- relevant docs were updated if behavior changed
- required checks were run and passed

If any check could not be run in the current environment, say exactly which check was not run and why.
