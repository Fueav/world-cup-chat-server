# 2026-06-29 Source-Backed Harness Refresh Implementation Plan

- Specification: `SPEC-SOURCE-BACKED-HARNESS-REFRESH-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: local `main` at `/Users/chris/AiProject/world-cup-chat-server`, starting from the pushed World Cup migration baseline.
- Scope summary: port the latest source-backed Harness workflow manifest, docs, virtual routing regressions, and validator checks from `moss-site/ai-first-go-template` while preserving this project's `docs/specifications/` and `docs/implementation-plans/` binding model.
- Out of scope: runtime/API/database changes, DockerHost deployment changes, and Go-template-only directory conventions.

## Change Steps

1. Inspect source template
   - Files/modules: remote `moss-site/ai-first-go-template`.
   - Behavior change: identify the latest Harness commit and changed files.
   - Data contract impact: none.
   - Tests to add/update: none.
   - Verification command: `gh repo view moss-site/ai-first-go-template --json defaultBranchRef,pushedAt`.
   - Rollback or compatibility note: source inspection is read-only.

2. Refresh Harness manifest and source docs
   - Files/modules: `docs/harness-workflows.json`, `docs/harness-workflows.md`, `docs/harness-source-analysis.md`.
   - Behavior change: adopt `source_set`, `adopted_principles`, workflow `source_ids`, workflow `principle_ids`, refreshed pattern vocabulary, `HARNESS-RUNTIME-LEGIBILITY`, and `HARNESS-EVAL-IMPROVEMENT-LOOP`.
   - Data contract impact: release artifact shape expands with source/principle summaries.
   - Tests to add/update: validator self-tests.
   - Verification command: `scripts/check_harness_workflows.sh`.
   - Rollback or compatibility note: retain project-specific docs spec/plan binding paths.

3. Refresh validator behavior
   - Files/modules: `scripts/check_harness_workflows.sh`, `scripts/check_harness_workflows_test.sh`.
   - Behavior change: reject stale schema names, unknown source/principle IDs, undocumented source analysis references, unknown patterns, and invalid virtual requirements.
   - Data contract impact: `.artifacts/release/harness_workflows.json` includes refreshed summary fields.
   - Tests to add/update: self-test cases for valid and invalid manifests.
   - Verification command: `scripts/check_harness_workflows_test.sh`.
   - Rollback or compatibility note: do not loosen AI-boundary or spec-contract checks.

4. Refresh virtual routing regressions
   - Files/modules: `docs/harness-virtual-requirements.json`.
   - Behavior change: replace legacy pattern names and removed `HARNESS-INTERACTIVE-ARTIFACT` with refreshed workflow expectations.
   - Data contract impact: none beyond validator input.
   - Tests to add/update: validator coverage for all virtual cases.
   - Verification command: `scripts/check_harness_workflows.sh`.
   - Rollback or compatibility note: all required patterns must be declared by the expected workflow class.

5. Final verification
   - Files/modules: full repository.
   - Behavior change: none.
   - Data contract impact: none.
   - Tests to add/update: no additional runtime tests expected.
   - Verification commands:
     - `scripts/check_harness_workflows_test.sh`
     - `scripts/check_harness_workflows.sh`
     - `scripts/check_spec_contract.sh`
     - `make test`
     - `AI_BOUNDARY_APPROVED=1 make verify-release`
   - Rollback or compatibility note: report any external dependency blocker instead of marking release-ready.

## Risk Controls

- Release-gate risk: keep `scripts/verify_release.sh` as the final authority.
- Source drift risk: require source and principle IDs in the manifest and docs.
- Project-layout risk: adapt template docs to this repo's `docs/specifications/` and `docs/implementation-plans/` layout.
- Runtime regression risk: avoid runtime/API/db edits.
- Boundary risk: use `AI_BOUNDARY_APPROVED=1` only for the user-requested script and Harness-doc changes.

## Completion Criteria

- Source-backed Harness docs and manifest are refreshed.
- Virtual requirements use only current workflow classes and patterns.
- Validator self-tests pass.
- Harness workflow validator passes on the real repo.
- Spec contract check passes.
- `make test` passes.
- `AI_BOUNDARY_APPROVED=1 make verify-release` passes or a concrete blocker is reported.
