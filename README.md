# aws-network-preflight

Declare your AWS network intent in YAML and verify that connectivity still matches it.

`aws-network-preflight` is an AWS-first CLI for platform, SRE, and networking teams. You describe the paths that must be reachable or must not be reachable, and the tool verifies those expectations using AWS-native analysis.

This repository is being built in phases. Today, `init`, `validate`, and `list-targets` are implemented. `run` and `explain` still exist as honest CLI scaffolds and do not execute Reachability Analyzer flows yet.

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

The repository is not at the full v1 feature set yet.

- `init`: available now
- `validate`: available now
- `list-targets`: available now for selector resolution only
- `run`: CLI scaffold only, not implemented yet
- `explain`: CLI scaffold only, not implemented yet

Phase 3 discovery is implemented. Phase 4 execution is not.

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
# scaffolded, not implemented yet:
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

For v1, ENI is the canonical execution target. EC2 instance is still an allowed user-facing input, but it is intended as a convenience input that will later normalize to one ENI before analysis runs.

For `list-targets`, instance resolution already normalizes to the primary ENI so the resolved target model stays precise and networking-oriented. Reachability Analyzer execution is still not implemented.
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

The current repository state is earlier than the full v1 target. Today it provides the project skeleton, config validation, examples, CI/test setup, and selector resolution for EC2 instances and ENIs via `list-targets`. Reachability Analyzer execution is still to be implemented.

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

- wire the existing target resolution flow into execution
- implement Reachability Analyzer-backed `run`
- implement detailed `explain`
- add JSON reporting for CI pipelines
- expand the supported target catalog carefully, not indiscriminately

## Project direction

The core promise of this tool is simple:

Declare your AWS network intent in YAML and verify that connectivity still matches it.

If we keep the implementation narrow, explicit, and AWS-native, it will stay more useful than a broader but vague tool.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
