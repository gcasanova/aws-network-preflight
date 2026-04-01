# aws-network-preflight

Declare your AWS network intent in YAML and verify that connectivity still matches it.

`aws-network-preflight` is an AWS-first CLI for platform, SRE, and networking teams. You describe the paths that must be reachable or must not be reachable, and the tool verifies those expectations using AWS-native analysis.

This repository is being built in phases. Today, `init`, `validate`, `list-targets`, `run`, and `explain` are implemented for the narrow v1 scope described below.

## Why this exists

AWS network posture drifts. Security groups change, routes move, NACLs get tightened, new attachments appear, and what used to work quietly stops working. Teams often notice that drift only after an outage, a failed deployment, or a late-night incident.

This project aims to make expected connectivity explicit:

- declare intent in version-controlled YAML
- validate that intent in CI or from a laptop
- lean on AWS-native analysis instead of hand-wavy heuristics

## v1 scope

The first release is intentionally narrow.

- AWS only
- CLI first
- Python 3.11+
- YAML config
- single-region-only for v1, using `defaults.region` as the effective region
- Assertion types:
  - `allow`
  - `deny`
- Analysis engine:
  - AWS Reachability Analyzer only
- Selector types:
  - `resource_id`
  - `arn`
  - `tags`
- Discovery target types supported in v1:
  - EC2 instances
  - Elastic Network Interfaces
- Every selector must resolve to exactly one resource

For v1, this is intentionally a single-region tool. The effective region comes from `defaults.region`, and configs that imply multi-region behavior are rejected for now. That is a scope choice for a precise first release, not a claim that multi-region support will never exist.
For v1, discovery is also intentionally limited to the standard commercial AWS partition (`aws`).

## Current status

The repository now covers the intended narrow v1 flow:

- `init`: create a starter config and examples
- `validate`: validate YAML structure and schema
- `list-targets`: resolve selectors to canonical execution targets
- `run`: execute all assertions through Reachability Analyzer
- `explain`: execute one assertion with more detailed output

The execution backend for v1 is AWS Reachability Analyzer only.
The repository also includes a JSON reporter for the current internal result objects, but JSON output is not yet exposed as a CLI flag.

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

## CLI usage

```bash
aws-network-preflight init
aws-network-preflight validate -f preflight.yaml
aws-network-preflight list-targets -f preflight.yaml
aws-network-preflight run -f preflight.yaml
aws-network-preflight explain -f preflight.yaml --id dev-to-shared-dns-allow
```

## Exit codes

- `0`: all assertions passed
- `1`: one or more assertions failed
- `2`: config or validation error
- `3`: runtime, AWS API, or authentication error

## IAM and auth model

v1 is designed to work with normal AWS credential flows.

- default AWS credential chain
- optional CLI `--profile` override
- per-account `role_arn` assumption for read-only analysis access

The base credentials need permission to call `sts:AssumeRole` when account roles are used. The assumed role itself will need the read and analysis permissions required by the implementation, including Reachability Analyzer APIs.

## v1 target model

For v1, ENI is the canonical execution target. EC2 instance is still an allowed user-facing input, but it is treated as a convenience input that normalizes to one primary ENI before Reachability Analyzer execution runs.

`list-targets`, `run`, and `explain` all consume the same resolved target model so selector behavior stays consistent across discovery and execution.
For tag-based selectors, v1 enforces strict uniqueness before normalization. If an EC2 instance and an ENI both match the same tags, that is treated as ambiguous even if the instance would normalize to that same ENI.

## Limitations

The tool is intentionally honest about what it will not do in v1.

- no Network Access Analyzer integration yet
- no active probes
- no public internet exposure checks
- no Transit Gateway, Cloud WAN, PrivateLink, or VPC Lattice specific logic
- no broad AWS resource coverage in v1 beyond EC2 instances and ENIs
- no web UI
- no auto-remediation
- no multi-cloud support
- no multi-region execution in v1
- no support for ambiguous selectors
- no non-commercial AWS partition support in v1

The current repository state is still intentionally narrow. It supports config validation, discovery for EC2 instances and ENIs, and Reachability Analyzer-backed execution for `allow` and `deny` assertions. It does not attempt broader AWS target coverage or non-v1 analysis modes.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy preflight
pytest
```

## Roadmap

- expose the existing JSON result model through a user-facing CLI output option
- expand the supported target catalog carefully, not indiscriminately

## Project direction

The core promise of this tool is simple:

Declare your AWS network intent in YAML and verify that connectivity still matches it.

If we keep the implementation narrow, explicit, and AWS-native, it will stay more useful than a broader but vague tool.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
