# 2026-06-29 Source-Backed Harness Refresh Specification

- Spec ID: `SPEC-SOURCE-BACKED-HARNESS-REFRESH-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: inspect the local latest Harness template in `/Users/chris/AiProject/ai-first-go-template` and port missing architecture into this project.
- Current behavior: this project has an older source-backed Harness manifest and validator, but it is missing the latest source set, Agent Team suitability contract, autonomy-governance workflow, and newer session/tool/eval-noise patterns.
- Problem: without this refresh, the repository can accept stale workflow definitions and miss governance evidence for higher-autonomy or managed-agent changes.
- Non-goals: no Go template project structure, no runtime/API/database behavior change, and no DockerHost deployment change.

## Product Semantics

- Maintainers choose a Harness workflow class for non-template specifications and implementation plans.
- The validator rejects undocumented source IDs, principle IDs, unknown patterns, missing Agent Team suitability, missing evidence contracts, stale virtual routing cases, and stale workflow bindings.
- Harness state remains repository documents plus generated `.artifacts/release/harness_workflows.json`.
- This project keeps its `docs/specifications/` and `docs/implementation-plans/` binding model instead of adopting the template's Go `specs/` directory shape.

## API / Interface Contract

- Public API behavior: no runtime API change.
- `scripts/check_harness_workflows.sh` validates the refreshed manifest schema and project spec/plan bindings.
- `scripts/check_harness_workflows_test.sh` exercises valid and invalid manifest cases for source sets, Agent Team suitability, unknown patterns, virtual requirements, and missing bindings.

## Architecture

- `docs/harness-workflows.json`: refreshed source-backed workflow manifest.
- `docs/harness-workflows.md`: project-adapted workflow catalog and binding rule.
- `docs/harness-source-analysis.md`: source and principle matrix.
- `docs/harness-virtual-requirements.json`: virtual routing regression cases.
- `docs/specifications/harness_workflows/`: Harness self-spec fragments.
- `scripts/check_harness_workflows.sh`: refreshed validator.
- `scripts/check_harness_workflows_test.sh`: validator regression tests.

## Harness Classification

- Expected gate: `HARNESS-SPEC-FIRST-FEATURE` because this changes repository workflow behavior and release validation.
- Harness mapping extension: add `HARNESS-AUTONOMY-GOVERNANCE`, Agent Team suitability on every workflow class, and source-backed patterns for permission classification, credential vaulting, session interfaces, agent-native telemetry, tool context economy, and eval-noise calibration.
- Focused verification commands:
  - `scripts/check_harness_workflows_test.sh`
  - `scripts/check_harness_workflows.sh`
  - `scripts/check_spec_contract.sh`
- Prerelease-grade verification command:
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- Harness manifest validates with current source IDs, principle IDs, Agent Team suitability, and refreshed workflow classes.
- Harness docs describe the refreshed classes and the project-specific spec/plan binding rule.
- Virtual requirements cover autonomy governance and use only declared workflow classes and patterns.
- Validator self-tests cover missing source sets, missing Agent Team data, invalid Agent Team suitability, unknown patterns, missing virtual requirements, and missing spec/plan bindings.
- Current project specs and plans continue to validate through `scripts/check_spec_contract.sh`.

## Review Notes

- Accepted assumption: the local template at `/Users/chris/AiProject/ai-first-go-template` is the latest source of truth for this sync.
- Rejected alternative: do not copy the template's Go-specific directory layout or weaken release validation to pass stale workflow records.
