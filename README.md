# aws-network-preflight

[![CI](https://github.com/gcasanova/aws-network-preflight/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/gcasanova/aws-network-preflight/actions/workflows/ci.yml) [![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](#installation)

Declare allowed and denied AWS connectivity in YAML and verify it with AWS Reachability Analyzer.

`aws-network-preflight` is a CLI for platform, SRE, and networking teams that want to describe expected AWS connectivity in version-controlled YAML and verify it with AWS Reachability Analyzer. It is useful when you need a practical way to catch drift in security groups, routes, and attachments before that drift turns into a broken deployment or an incident.

It is intentionally narrow: v1 focuses on single-region AWS connectivity validation for EC2 instances and ENIs.

## Why this exists

AWS connectivity changes over time. Security groups get edited, routes move, NACLs tighten, new attachments appear, and paths that used to work quietly stop working.

This project exists to make expected connectivity explicit and testable:

- declare intent in YAML
- verify it locally or in CI
- use AWS-native analysis instead of hand-built network heuristics

## Installation

Python 3.11+ is required.

Install from source:

```bash
git clone https://github.com/gcasanova/aws-network-preflight.git
cd aws-network-preflight
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

The CLI uses the default AWS credential chain by default, supports `--profile`, and can assume per-account `role_arn` values defined in the config.

You will need AWS credentials and permissions that can read the referenced resources and run Reachability Analyzer in the configured accounts.

## Quick start

Create a starter config:

```bash
aws-network-preflight init
```

Validate the starter config to confirm the CLI is installed and working:

```bash
aws-network-preflight validate -f preflight.yaml
```

Then edit `preflight.yaml` with your real AWS account details, role ARNs, regions, and selectors before using `list-targets` or `run`.

Resolve the configured targets without running analysis:

```bash
aws-network-preflight list-targets -f preflight.yaml
```

Run the assertions:

```bash
aws-network-preflight run -f preflight.yaml
```

For CI-friendly output, `run` and `explain` also support `--format json`.

## Example config

```yaml
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
```

## Example commands and output

Common tasks:

```bash
# run all configured assertions
aws-network-preflight run -f preflight.yaml

# inspect one assertion in detail
aws-network-preflight explain -f preflight.yaml --id dev-to-shared-dns-allow

# emit machine-readable output for CI
aws-network-preflight run -f preflight.yaml --format json
```

Text output from `run`:

```text
                               Assertion Results
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Assertion ID             ┃ Expected      ┃ Actual        ┃ Status ┃ Analysis ID           ┃ Detail                                                       ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ dev-to-shared-dns-allow  │ reachable     │ reachable     │ passed │ nia-0abc123def456789 │ Expected reachable and Reachability Analyzer reported       │
│                          │               │               │        │                       │ reachable.                                                  │
│ dev-to-prod-db-deny      │ not reachable │ reachable     │ failed │ nia-0123abc456def789 │ Expected not reachable but Reachability Analyzer reported   │
│                          │               │               │        │                       │ reachable.                                                  │
└──────────────────────────┴───────────────┴───────────────┴────────┴───────────────────────┴──────────────────────────────────────────────────────────────┘
Passed: 1  Failed: 1  Errors: 0
```

JSON output from `run --format json`:

```json
{
  "error_count": 0,
  "failed_count": 1,
  "passed_count": 1,
  "results": [
    {
      "actual_outcome": "reachable",
      "assertion_id": "dev-to-shared-dns-allow",
      "expected_outcome": "reachable",
      "status": "passed"
    },
    {
      "actual_outcome": "reachable",
      "assertion_id": "dev-to-prod-db-deny",
      "expected_outcome": "not_reachable",
      "status": "failed"
    }
  ]
}
```

Exit codes:

- `0`: all assertions passed
- `1`: one or more assertions failed
- `2`: config or validation error
- `3`: runtime, AWS API, or authentication error

## Design choices

The scope is intentionally narrow because the goal is a reliable v1, not a vague networking framework.

- AWS-first because the tool is built around AWS-native analysis and AWS account boundaries, not generic abstractions.
- Single-region-only in v1 because discovery and execution are much easier to reason about when every assertion runs in one explicit effective region from `defaults.region`.
- Reachability Analyzer only in v1 because one trustworthy engine is more useful than several partially-supported analysis modes.
- ENI as the canonical execution target because it is the most precise AWS networking anchor for path analysis.
- EC2 instance as a convenience input because it keeps the CLI practical while still normalizing execution to one concrete ENI.
- Narrow target-family support because public v1 credibility comes from being explicit about what the tool does support, not by implying it solves all of AWS networking.

## Limitations

- v1 is single-region-only
- v1 uses AWS Reachability Analyzer only
- supported target families are limited to EC2 instances and ENIs
- selectors must resolve to exactly one supported resource
- tag ambiguity is a hard failure
- only the standard commercial AWS partition (`aws`) is supported
- no Network Access Analyzer, active probes, internet exposure checks, or service-specific logic for TGW, Cloud WAN, PrivateLink, or VPC Lattice

## Development

Install development dependencies and run the local checks:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy preflight
pytest
```

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
