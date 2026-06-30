# Harness Workflows Harness Binding

Spec ID: `SPEC-HARNESS-WORKFLOW-001`

Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Why

Harness workflow changes alter the project contract for future feature work, so they must follow the spec-first feature workflow and finish at the release harness.

## Required Evidence

- Source analysis: `docs/harness-source-analysis.md`
- Workflow catalog: `docs/harness-workflows.json`
- Human-readable workflow guide: `docs/harness-workflows.md`
- Virtual requirements: `docs/harness-virtual-requirements.json`
- Focused verification command: `scripts/check_harness_workflows.sh`
- Validator regression command: `scripts/check_harness_workflows_test.sh`
- Prerelease command: `scripts/verify_release.sh`
- Artifact path: `.artifacts/release/harness_workflows.json`
