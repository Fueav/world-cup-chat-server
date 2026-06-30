# 2026-06-29 Source-Backed Harness Refresh Implementation Plan

- Specification: `SPEC-SOURCE-BACKED-HARNESS-REFRESH-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: local `main` at `/Users/chris/AiProject/world-cup-chat-server`; preserve unrelated dirty runtime/test/spec changes.
- Scope summary: port the latest local Harness workflow manifest, docs, virtual routing regressions, self-spec fragments, and validator checks from `/Users/chris/AiProject/ai-first-go-template` while preserving this project's `docs/specifications/` and `docs/implementation-plans/` binding model.
- Out of scope: runtime/API/database changes, DockerHost deployment changes, and Go-template-only directory conventions.

## Change Steps

1. Refresh Harness manifest and source docs
   - Files/modules: `docs/harness-workflows.json`, `docs/harness-workflows.md`, `docs/harness-source-analysis.md`.
   - Behavior change: adopt current source set, adopted principles, Agent Team suitability, `HARNESS-AUTONOMY-GOVERNANCE`, and refreshed pattern vocabulary.
   - Verification command: `scripts/check_harness_workflows.sh`.

2. Refresh validator behavior
   - Files/modules: `scripts/check_harness_workflows.sh`, `scripts/check_harness_workflows_test.sh`.
   - Behavior change: reject stale schema names, missing Agent Team metadata, unknown source/principle IDs, undocumented source references, unknown patterns, invalid virtual requirements, and missing spec/plan bindings.
   - Verification command: `scripts/check_harness_workflows_test.sh`.

3. Refresh Harness self-spec and virtual regressions
   - Files/modules: `docs/specifications/harness_workflows/`, `docs/harness-virtual-requirements.json`.
   - Behavior change: keep the workflow framework executable and require autonomy-governance routing coverage.
   - Verification command: `scripts/check_harness_workflows.sh`.

4. Final verification
   - Commands:
     - `scripts/check_harness_workflows_test.sh`
     - `scripts/check_harness_workflows.sh`
     - `scripts/check_spec_contract.sh`
     - `AI_BOUNDARY_APPROVED=1 make verify-release`

## Risk Controls

- Keep `scripts/verify_release.sh` as the final authority.
- Preserve the docs-based spec/plan layout.
- Avoid unrelated dirty files and runtime/API/db edits.
- Use `AI_BOUNDARY_APPROVED=1` only because scripts are approval-required and this sync is explicitly requested.

## Completion Criteria

- Harness docs, manifest, virtual requirements, and self-spec fragments are refreshed.
- Validator self-tests pass.
- Harness workflow validator passes on the real repo.
- Spec contract check passes.
- Release gate passes or a concrete blocker is reported.
