# 2026-06-29 Source-Backed Harness Refresh Specification

- Spec ID: `SPEC-SOURCE-BACKED-HARNESS-REFRESH-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: inspect the latest Harness update in `moss-site/ai-first-go-template`, specifically commit `19f276003a78ca975194cc2d58dfeb7d32d67458`, and port the missing source-backed Harness pieces into this project.
- Current behavior: this project had an older Harness workflow manifest and regression validator that used `source_reading_set`, `principles`, legacy strategy fields, and older pattern names.
- Problem: without the refresh, this repository can drift from the latest source-backed Harness contract and keep accepting obsolete workflow classes or pattern vocabulary.
- Non-goals: no Go template project structure, no Go runtime changes, no chat API/runtime/database behavior change, and no DockerHost deployment change.

## Product Semantics

- User/operator workflow:
  - Maintainers choose a Harness workflow class for non-template specs and implementation plans.
  - The validator rejects undocumented source IDs, principle IDs, unknown patterns, missing evidence contracts, and stale workflow bindings.
- State model:
  - Harness state remains repository documents plus generated `.artifacts/release/harness_workflows.json`.
- Ownership and identity rules:
  - This project keeps its `docs/specifications/` and `docs/implementation-plans/` binding model instead of adopting the template's `specs/` directory shape.
- Compatibility and migration expectations:
  - Existing World Cup runtime behavior is unaffected.
  - Existing release verification continues to call `scripts/check_harness_workflows.sh`.

## API / Interface Contract

- Public API behavior: no runtime API change.
- Script contract:
  - `scripts/check_harness_workflows.sh` must validate the refreshed manifest schema.
  - `scripts/check_harness_workflows_test.sh` must exercise both valid and invalid manifest cases for the refreshed schema.
- Manifest contract:
  - Top-level `source_set` and `adopted_principles` are required.
  - Each workflow class must include valid `source_ids` and `principle_ids`.
  - Workflow patterns must come from the refreshed source-backed vocabulary.

## Data / Schema / Projection Impact

- Tables, migrations, and runtime caches: none.
- Release artifacts:
  - The Harness validator writes `.artifacts/release/harness_workflows.json` with source, principle, workflow, spec, plan, and virtual requirement summaries.

## Architecture

- Modules/files expected to change:
  - `docs/harness-workflows.json`: refreshed source-backed workflow manifest.
  - `docs/harness-workflows.md`: project-adapted workflow catalog and binding rule.
  - `docs/harness-source-analysis.md`: source and principle matrix.
  - `docs/harness-virtual-requirements.json`: refreshed virtual routing regression cases.
  - `scripts/check_harness_workflows.sh`: refreshed schema validator.
  - `scripts/check_harness_workflows_test.sh`: validator regression tests.
- Transaction/concurrency boundaries: none.
- Observability/logging/metrics:
  - Release evidence remains under `.artifacts/release/`.
- Rollback strategy:
  - Revert the Harness docs/scripts as a single patch if the refreshed contract blocks valid workflows.

## Harness Classification

- Expected gate(s):
  - `HARNESS-SPEC-FIRST-FEATURE` because this changes repository workflow behavior and release validation.
- Whether harness mapping must be extended:
  - Yes. The refreshed manifest replaces the obsolete `HARNESS-INTERACTIVE-ARTIFACT` class with `HARNESS-RUNTIME-LEGIBILITY` and `HARNESS-EVAL-IMPROVEMENT-LOOP`.
- Focused verification commands:
  - `scripts/check_harness_workflows_test.sh`
  - `scripts/check_harness_workflows.sh`
  - `scripts/check_spec_contract.sh`
- Prerelease-grade verification commands:
  - `make test`
  - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - Harness manifest validates with source IDs and principle IDs.
  - Harness docs describe the refreshed workflow classes and project-specific spec/plan binding rule.
  - Virtual requirements no longer reference removed classes, patterns, or strategy fields.
  - Validator regression tests cover missing source sets, unknown sources, unknown principles, stale docs references, virtual requirement drift, and missing spec/plan bindings.
- Compatibility:
  - Current project specs and plans continue to validate through `scripts/check_spec_contract.sh`.
  - Existing release gate continues to call the Harness validator.
- Operational:
  - Release verification produces the standard summary artifact.

## Review Notes

- Accepted assumptions:
  - The latest template commit inspected is `19f276003a78ca975194cc2d58dfeb7d32d67458`.
  - This project should keep its existing docs directory layout.
- Rejected alternatives:
  - Do not copy the template's Go-specific `specs/` layout.
  - Do not weaken release validation to pass stale workflow records.
