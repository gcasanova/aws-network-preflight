# aws-network-preflight v1 Plan

## Product statement

`aws-network-preflight` is an AWS-first CLI that lets platform, SRE, and networking teams declare expected network intent in YAML and verify that AWS connectivity still matches that intent.

The initial release should feel credible, boring in the right ways, and narrowly scoped. The goal is a polished v1, not a framework.

## Current status

- config validation is implemented
- selector resolution is implemented for `list-targets`
- supported discovery inputs are `resource_id`, `arn`, and `tags`
- supported discovery target families are EC2 instances and ENIs
- EC2 instances normalize to their primary ENI in the resolved target model
- Reachability Analyzer execution is not implemented yet

## v1 scope

### Included

- AWS-only CLI
- YAML configuration
- Two assertion types:
  - `allow`: the path must be reachable
  - `deny`: the path must not be reachable
- One analysis backend:
  - AWS Reachability Analyzer
- Selector types:
  - `resource_id`
  - `arn`
  - `tags`
- Single effective region for v1:
  - `defaults.region` is the only execution region
- Single AWS partition for v1:
  - standard commercial `aws` only
- Supported AWS target types for v1:
  - EC2 instance IDs
  - Elastic Network Interface IDs
- CI-friendly exit codes and output
- Local execution or CI execution with standard AWS credentials

### Not included

- Network Access Analyzer
- Internet exposure checks
- Active probes
- Failover or game day workflows
- Cloud WAN logic
- Transit Gateway logic
- VPC Lattice
- PrivateLink
- Web UI
- Auto-remediation
- Multi-cloud support

## Architecture decisions

1. Keep one config model and one execution flow.
   The CLI should load a single YAML document into strict Pydantic models. We will reject unknown fields and fail fast on ambiguous selectors, duplicate assertion IDs, and broken account references.

2. Keep auth explicit and boring.
   A small auth module will wrap:
   - default AWS credential chain
   - optional `--profile` override
   - per-account `role_arn` assumption

3. Keep v1 single-region-only.
   v1 should use `defaults.region` as the single effective region for validation, discovery, and execution. We should not add endpoint-level region overrides yet. The existing account region lists may remain in the schema for future evolution, but v1 should reject configs that imply multi-region behavior.

4. Resolve selectors before analysis.
   `list-targets`, `run`, and `explain` should all share the same selector-resolution path. Every selector must resolve to exactly one target. Zero or multiple matches are hard failures.

5. Start with a narrow resource-lookup strategy.
   v1 will support only two concrete target types:
   - EC2 instances
   - Elastic Network Interfaces

   This is intentionally narrower than the selector schema alone might suggest. Those two resource types are common, easy to reason about, and map cleanly onto AWS network analysis workflows. Broader support is out of scope for v1 because many AWS resources introduce service-specific semantics, indirect network attachments, or multi-step resolution rules that would make the first release less predictable.

6. Make selector resolution opinionated.
   Selector resolution should not be generic or best-effort in v1. The resolver should identify one concrete EC2 instance or one concrete ENI and fail otherwise. We should avoid claiming support for arbitrary ARNs or taggable AWS resources just because they can be described in YAML.

7. Treat Reachability Analyzer as the source of truth for v1.
   `allow` assertions pass when the analysis result is reachable. `deny` assertions pass when the analysis result is not reachable. We should surface the AWS-native explanation rather than inventing our own network model.

8. Separate orchestration from presentation.
   The runner should produce plain result objects. Console and JSON reporters should format those results without owning business logic.

9. Avoid a plugin architecture.
   Future engines can fit behind a small internal interface, but v1 should not introduce registries, plugin loading, or extension frameworks.

## v1 target model

The supported user-facing AWS target types for v1 are explicit:

- EC2 instance IDs such as `i-0123456789abcdef0`
- Elastic Network Interface IDs such as `eni-0123456789abcdef0`

The internal execution direction for v1 is also explicit:

- ENI is the canonical execution target
- EC2 instance is a convenience input type

The implemented discovery model already normalizes an instance selector to one specific primary ENI in the resolved target output used by `list-targets`. Reachability Analyzer execution is still deferred to Phase 4, but the internal target model is already aligned to ENI-first execution.

### Why ENI is the canonical execution target

- Network policy evaluation in AWS ultimately happens at the network interface layer more than at the instance abstraction layer.
- ENIs provide a more precise anchor for security groups, subnets, routing context, and packet-path analysis.
- Treating instances as a convenience input keeps the CLI ergonomic without making the execution model fuzzy.

### Why broader resource support is out of scope

- Reachability Analyzer may support additional endpoint shapes, but that does not mean this tool should claim them in v1.
- Load balancers, RDS, Lambda, ECS tasks, Transit Gateway attachments, and other managed resources often require extra translation to underlying network interfaces or service-specific handling.
- Tag-based discovery across many AWS services can become ambiguous quickly and makes error handling less credible.
- A narrower target catalog keeps failure modes understandable and reduces the risk of the tool appearing broader than it really is.

For a public v1, it is better to support a small set of targets well than to advertise a wide surface area with caveats everywhere.

### Selector resolution assumptions and constraints

- Every selector must resolve to exactly one resource.
- Resolution is account-scoped by the `account` field on each endpoint.
- v1 is intentionally single-region-only. `defaults.region` is the one effective region for the whole run.
- v1 is intentionally scoped to the standard commercial AWS partition (`aws`) only.
- `accounts.*.regions` may remain in the config shape for now, but in v1 each account must declare exactly one region and it must match `defaults.region`.
- No endpoint-level `region` field is supported in v1.
- `resource_id` is valid only when it names a supported v1 target type.
  We use a deliberately simple AWS-realistic pattern for EC2 instance and ENI IDs: 8 or 17 lowercase hex characters after the prefix.
- `arn` is valid only when it refers to a supported v1 target type and can be mapped unambiguously to one region/account/resource.
  Direct callers that bypass the normal account-aware resolution flow must provide reliable effective account identity for ARN validation.
- `tags` are valid only when they resolve to exactly one supported v1 target type within the configured account and region scope.
- Tag ambiguity is enforced before normalization. If an instance and an ENI both match the same tags, v1 treats that as ambiguous even if the instance would normalize to that ENI.
- If a selector matches zero resources, the assertion should fail clearly as a config/runtime error.
- If a selector matches multiple resources, the assertion should fail clearly rather than picking one.
- We should not silently translate unsupported resources to some underlying network object in v1.

This single-region rule is an intentional v1 simplification to keep discovery and execution semantics precise. It is not meant as a permanent product limitation.

## Module breakdown

- `preflight/cli.py`
  - Typer entrypoint
  - command wiring
  - exit-code handling

- `preflight/models.py`
  - strict Pydantic schema for config
  - validation rules for selectors, assertions, and account references

- `preflight/config.py`
  - YAML loading
  - validation error formatting
  - starter config template

- `preflight/auth.py`
  - base boto3 session creation
  - optional profile override
  - per-account role assumption

- `preflight/discovery.py`
  - selector resolution
  - unique-match enforcement
  - shared logic for `list-targets`, `run`, and `explain`

- `preflight/runner.py`
  - assertion orchestration
  - pass/fail evaluation
  - summary result objects

- `preflight/engines/reachability_analyzer.py`
  - Reachability Analyzer create/start/read/cleanup flow
  - mapping AWS results into runner-friendly outputs

- `preflight/reporters/console.py`
  - rich terminal output for validation, resolution, run summaries, and explain output

- `preflight/reporters/json_report.py`
  - machine-readable JSON output for CI integrations

- `preflight/exit_codes.py`
  - stable, named exit-code constants

## Milestone order

### Phase 1

- inspect repo state
- document the plan
- lock v1 boundaries

### Phase 2

- create package structure
- add `pyproject.toml`
- add base Typer CLI
- implement YAML loading and Pydantic validation
- add starter example config
- draft README
- add CI, Ruff, mypy, pytest
- add validation-focused tests

### Phase 3

- implement selector resolution for `resource_id`, `arn`, and `tags`
- treat ENIs as the canonical execution target
- normalize EC2 instance inputs to one ENI before execution
- constrain resolution to EC2 instances and ENIs only
- add `list-targets`
- make `validate` production-ready

Phase 3 is now focused on discovery only. `list-targets` is the implemented entrypoint for that behavior. `run` and `explain` remain intentionally unimplemented for execution.

### Phase 4

- implement Reachability Analyzer execution flow
- add `run`
- add `explain`
- add console and JSON reporting
- harden runtime and AWS error handling

## Explicit non-goals

- Modeling all AWS networking concepts in-house
- Supporting ambiguous selectors
- Supporting every Reachability Analyzer resource type in v1
- Supporting arbitrary AWS resources via generic ARN or tag matching
- Building a long-lived control plane or service
- Hiding AWS limitations behind vague output
- Shipping speculative abstractions before the first engine works cleanly

## Open decisions to document as we build

- JSON output shape for CI consumers
- How aggressively to clean up Reachability Analyzer paths and analyses after execution
