# aws-network-preflight

[![CI](https://github.com/gcasanova/aws-network-preflight/actions/workflows/ci.yml/badge.svg?branch=main&event=push)](https://github.com/gcasanova/aws-network-preflight/actions/workflows/ci.yml) [![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](#installation)

Declare allowed and denied AWS connectivity in YAML and verify it with AWS Reachability Analyzer.

`aws-network-preflight` is a Python CLI for platform, SRE, and networking teams that want to describe expected AWS connectivity in version-controlled YAML and verify it with AWS Reachability Analyzer. It helps catch drift in security groups, routes, and attachments before that drift turns into a broken deployment or an incident.

The scope is intentionally narrow. v1 focuses on single-region AWS connectivity validation for EC2 instances and ENIs.

## Why this exists

AWS connectivity changes over time. Security groups get edited, routes move, NACLs tighten, new attachments appear, and paths that used to work quietly stop working.

This project exists to make expected connectivity explicit and testable:

- declare intent in YAML
- verify it locally or in CI
- use AWS-native analysis instead of hand-built network heuristics

## Installation

Python 3.11+ is required.

### Recommended install

Install from PyPI:

```bash
pipx install aws-network-preflight
```

That gives you a globally available CLI in an isolated environment.

### Install from source

Install from source for local development:

```bash
git clone https://github.com/gcasanova/aws-network-preflight.git
python3 -m venv ~/venvs/anp
source ~/venvs/anp/bin/activate
pip install -e ./aws-network-preflight
```

This keeps the virtual environment outside the repository instead of creating a local `.venv` inside the source tree.

`anp` is the short alias for `aws-network-preflight`.

The CLI uses the AWS credential chain and supports profile-based authentication. For multi-account setups, logical accounts can also define per-account `role_arn` values to assume.

You will need AWS credentials and permissions that can read the referenced resources and run Reachability Analyzer in the configured accounts.

## Quick start

Create a starter config:

```bash
anp init
```

Edit `preflight.yaml` with your real AWS profile, region, accounts, and selectors.

Validate the config:

```bash
anp validate -f preflight.yaml
```

Resolve the configured targets without running analysis:

```bash
anp list-targets -f preflight.yaml
```

Run the assertions:

```bash
anp run -f preflight.yaml
```

Inspect one assertion in detail:

```bash
anp explain -f preflight.yaml --id client-to-server-443-allow
```

For CI-friendly output, `run` and `explain` also support `--format json`.

## Minimal example config

This is the simplest same-account profile-based shape:

```yaml
version: 1

defaults:
  region: us-east-1
  auth:
    mode: profile
    profile: default

accounts:
  lab:
    regions: [us-east-1]

assertions:
  - id: client-to-server-443-allow
    type: allow
    source:
      account: lab
      selector:
        tags:
          Name: client
    destination:
      account: lab
      selector:
        tags:
          Name: server
    protocol: tcp
    port: 443

  - id: client-to-server-80-deny
    type: deny
    source:
      account: lab
      selector:
        tags:
          Name: client
    destination:
      account: lab
      selector:
        tags:
          Name: server
    protocol: tcp
    port: 80
```

`role_arn` is optional for simple same-account profile-based usage. For multi-account configurations, each logical account can also define a `role_arn` to assume.

## Cross-account example

For multi-account setups, logical accounts can carry their own role assumptions:

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

## Recommended IAM permissions

At a minimum, the CLI usually needs enough access to discover the selected resources, run Reachability Analyzer, and identify the effective AWS account for each configured session.

- Discovery: `ec2:DescribeInstances`, `ec2:DescribeNetworkInterfaces`
- Reachability Analyzer execution: `ec2:CreateNetworkInsightsPath`, `ec2:DeleteNetworkInsightsPath`, `ec2:StartNetworkInsightsAnalysis`, `ec2:DescribeNetworkInsightsAnalyses`, `ec2:DeleteNetworkInsightsAnalysis`
- Identity and account resolution: `sts:GetCallerIdentity`
- Cross-account usage with `role_arn`: `sts:AssumeRole`

The exact policy can vary by account structure and whether you use same-account credentials or cross-account role assumption.

## Example commands and output

Common tasks:

```bash
# run all configured assertions
anp run -f preflight.yaml

# inspect one assertion in detail
anp explain -f preflight.yaml --id client-to-server-443-allow

# emit machine-readable output for CI
anp run -f preflight.yaml --format json
```

Text output from `run`:

```text
                                                                     Assertion Results
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Assertion ID                ┃ Expected      ┃ Actual        ┃ Status ┃ Analysis ID          ┃ Detail                                                     ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ client-to-server-443-allow  │ reachable     │ reachable     │ passed │ nia-0abc123def456789 │ Expected reachable and Reachability Analyzer reported      │
│                             │               │               │        │                      │ reachable.                                                 │
│ client-to-server-80-deny    │ not reachable │ reachable     │ failed │ nia-0123abc456def789 │ Expected not reachable but Reachability Analyzer reported  │
│                             │               │               │        │                      │ reachable.                                                 │
└─────────────────────────────┴───────────────┴───────────────┴────────┴──────────────────────┴────────────────────────────────────────────────────────────┘
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
      "assertion_id": "client-to-server-443-allow",
      "expected_outcome": "reachable",
      "status": "passed"
    },
    {
      "actual_outcome": "reachable",
      "assertion_id": "client-to-server-80-deny",
      "expected_outcome": "not_reachable",
      "status": "failed"
    }
  ]
}
```

## Troubleshooting

- `Unable to locate credentials`: the AWS credential chain did not find usable credentials. Check your environment, local AWS config, or the profile referenced in `defaults.auth.profile`.
- `AccessDenied` or `UnauthorizedOperation`: the active credentials can reach AWS but do not have one or more required EC2 or STS permissions. Check discovery permissions, Reachability Analyzer permissions, and `sts:AssumeRole` when `role_arn` is configured.
- Tag selector matches no resources: the selected account, region, or tag values do not resolve to a supported v1 target. Check `defaults.region`, the endpoint `account`, and the exact tag key and value on the EC2 instance or ENI.
- Tag selector matches multiple resources: the selector is not unique within the configured account and region. Tighten the tag set until it resolves to exactly one EC2 instance or ENI.
- Reachability Analyzer finished with `failed` or another unexpected status: AWS did not produce a normal successful analysis result. Check the detailed error output first, then confirm the source and destination resolved to the intended ENIs and that the execution account has the required Reachability Analyzer permissions.

## Exit codes

- `0`: all assertions passed
- `1`: one or more assertions failed
- `2`: config or validation error
- `3`: runtime, AWS API, or authentication error

## Demo

A short terminal demo showing config validation, target resolution, real Reachability Analyzer execution, and assertion inspection will be added here.

<!-- Example:
[Demo video](...)
![aws-network-preflight demo](docs/demo/anp-demo.gif)
-->

## Design choices

The scope is intentionally narrow because the goal is a reliable v1, not a vague networking framework.

- AWS-first because the tool is built around AWS-native analysis and AWS account boundaries, not generic abstractions.
- Single-region-only in v1 because discovery and execution are easier to reason about when every assertion runs in one explicit effective region from `defaults.region`.
- Reachability Analyzer only in v1 because one trustworthy engine is more useful than several partially supported analysis modes.
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
git clone https://github.com/gcasanova/aws-network-preflight.git
python3 -m venv ~/venvs/anp-dev
source ~/venvs/anp-dev/bin/activate
pip install -e "./aws-network-preflight[dev]"
cd aws-network-preflight
ruff check .
ruff format --check .
mypy preflight
pytest
```

## Contributing

Issues, feedback, and contributions are welcome.

A good first contribution is usually one of these:

- improve documentation and examples
- tighten validation and error messages
- add focused test coverage for supported v1 behavior
- improve local UX without broadening the scope carelessly

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
