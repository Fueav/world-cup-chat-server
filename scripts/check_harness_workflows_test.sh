#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/check_harness_workflows.sh"
TMP_DIR="$(mktemp -d)"

trap 'rm -rf "$TMP_DIR"' EXIT

write_doc() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'DOC'
# Harness Workflows

## Core Patterns

| Pattern | Meaning |
| --- | --- |
| `token-budget` | Declare budget. |
| `resumable-evidence` | Write durable evidence. |

## HARNESS-FOCUSED-CHANGE

Focused changes stay in one context with release evidence.
DOC
}

write_source_doc() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'DOC'
# Harness Source Analysis

| Source ID | Provider | Source |
| --- | --- | --- |
| `openai-codex-manual` | OpenAI | https://developers.openai.com/codex/codex-manual.md |

| Principle ID | Project meaning | Main sources |
| --- | --- | --- |
| `release-gate-hard-authority` | Release remains the hard gate. | `openai-codex-manual` |
DOC
}

write_valid_manifest() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'JSON'
{
  "version": 1,
  "source_set": [
    {
      "id": "openai-codex-manual",
      "provider": "OpenAI",
      "title": "Codex manual",
      "url": "https://developers.openai.com/codex/codex-manual.md",
      "status": "adopted"
    }
  ],
  "adopted_principles": [
    {
      "id": "release-gate-hard-authority",
      "summary": "Release harness remains the hard gate.",
      "source_ids": ["openai-codex-manual"]
    }
  ],
  "workflow_classes": [
    {
      "id": "HARNESS-FOCUSED-CHANGE",
      "name": "Focused Change",
      "purpose": "Use the default release harness for narrow scoped changes.",
      "source_ids": ["openai-codex-manual"],
      "principle_ids": ["release-gate-hard-authority"],
      "use_when": ["A change fits in one context window."],
      "patterns": ["token-budget", "resumable-evidence"],
      "isolation": {
        "worktree": "optional",
        "context": "single-agent",
        "quarantine_untrusted_inputs": false
      },
      "verification": {
        "primary_command": "scripts/verify_release.sh",
        "adversarial_review": false,
        "rubric": ["Specification still matches implementation."]
      },
      "stop_conditions": ["Focused tests and release gate pass."],
      "evidence": [".artifacts/release/summary.json"],
      "budget": {
        "token_budget": "bounded-by-task",
        "parallelism": "none"
      },
      "human_escalation": ["Approval-required paths changed."]
    }
  ]
}
JSON
}

write_virtual_requirements() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'JSON'
{
  "version": 1,
  "cases": [
    {
      "id": "virtual-small-doc-fix",
      "request": "Fix a typo in README.",
      "expected_workflow": "HARNESS-FOCUSED-CHANGE",
      "required_patterns": ["token-budget", "resumable-evidence"],
      "rationale": "Small edits should stay focused."
    }
  ]
}
JSON
}

write_bound_spec() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'SPEC'
# Bound Specification

Spec ID: `SPEC-BOUND-001`
Workflow Class: `HARNESS-FOCUSED-CHANGE`
SPEC
}

write_unbound_spec() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'SPEC'
# Unbound Specification

Spec ID: `SPEC-UNBOUND-001`
SPEC
}

write_bound_plan() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'PLAN'
# Bound Plan

- Specification: `docs/specifications/bound.md`
- Workflow Class: `HARNESS-FOCUSED-CHANGE`
PLAN
}

write_unbound_plan() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  cat >"$file" <<'PLAN'
# Unbound Plan

- Specification: `docs/specifications/unbound.md`
PLAN
}

run_success() {
  local name="$1"
  shift
  if "$@" >"$TMP_DIR/$name.out" 2>"$TMP_DIR/$name.err"; then
    printf 'PASS %s\n' "$name"
  else
    printf 'expected success for %s\n' "$name" >&2
    cat "$TMP_DIR/$name.out" >&2 || true
    cat "$TMP_DIR/$name.err" >&2 || true
    exit 1
  fi
}

run_failure() {
  local name="$1"
  shift
  if "$@" >"$TMP_DIR/$name.out" 2>"$TMP_DIR/$name.err"; then
    printf 'expected failure for %s\n' "$name" >&2
    cat "$TMP_DIR/$name.out" >&2 || true
    exit 1
  fi
  printf 'PASS %s\n' "$name"
}

DOC="$TMP_DIR/docs/harness-workflows.md"
SOURCE_DOC="$TMP_DIR/docs/harness-source-analysis.md"
MANIFEST="$TMP_DIR/docs/harness-workflows.json"
VIRTUAL_REQUIREMENTS="$TMP_DIR/docs/harness-virtual-requirements.json"
SPECS_ROOT="$TMP_DIR/docs/specifications"
PLANS_ROOT="$TMP_DIR/docs/implementation-plans"

write_doc "$DOC"
write_source_doc "$SOURCE_DOC"
write_valid_manifest "$MANIFEST"
write_virtual_requirements "$VIRTUAL_REQUIREMENTS"
write_bound_spec "$SPECS_ROOT/bound.md"
write_bound_plan "$PLANS_ROOT/bound.md"

run_success valid_manifest env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/valid" \
  "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/missing_stop.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0].pop("stop_conditions")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_stop_conditions env \
  HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/missing_stop.json" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_stop" \
  "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/unknown_pattern.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0]["patterns"].append("unknown-pattern")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_pattern env \
  HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/unknown_pattern.json" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_pattern" \
  "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/missing_source_set.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data.pop("source_set")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_source_set env \
  HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/missing_source_set.json" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_source_set" \
  "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/unknown_source.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0]["source_ids"] = ["missing-official-source"]
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_source env \
  HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/unknown_source.json" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_source" \
  "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/unknown_principle.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0]["principle_ids"] = ["missing-principle"]
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_principle env \
  HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/unknown_principle.json" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_principle" \
  "$SCRIPT"

MISSING_SOURCE_DOC="$TMP_DIR/docs/missing-source-doc.md"
cat >"$MISSING_SOURCE_DOC" <<'DOC'
# Harness Source Analysis

No source IDs or URLs here.
DOC
run_failure missing_source_doc_reference env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$MISSING_SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_source_doc" \
  "$SCRIPT"

MISSING_DOC="$TMP_DIR/docs/missing-workflow-doc.md"
cat >"$MISSING_DOC" <<'DOC'
# Harness Workflows

No workflow IDs here.
DOC
run_failure missing_workflow_doc_reference env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$MISSING_DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_workflow_doc" \
  "$SCRIPT"

python3 - "$VIRTUAL_REQUIREMENTS" "$TMP_DIR/unknown_virtual_workflow.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["cases"][0]["expected_workflow"] = "HARNESS-NOT-REAL"
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_virtual_workflow env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$TMP_DIR/unknown_virtual_workflow.json" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_virtual_workflow" \
  "$SCRIPT"

python3 - "$VIRTUAL_REQUIREMENTS" "$TMP_DIR/missing_virtual_pattern.json" <<'PY'
import json
import sys

source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["cases"][0]["required_patterns"] = ["quarantine"]
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_virtual_pattern env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$TMP_DIR/missing_virtual_pattern.json" \
  HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_virtual_pattern" \
  "$SCRIPT"

UNBOUND_SPECS_ROOT="$TMP_DIR/unbound/specifications"
UNBOUND_PLANS_ROOT="$TMP_DIR/unbound/implementation-plans"
write_unbound_spec "$UNBOUND_SPECS_ROOT/unbound.md"
write_bound_plan "$UNBOUND_PLANS_ROOT/bound.md"
run_failure missing_spec_workflow_binding env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$UNBOUND_SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$UNBOUND_PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_spec_binding" \
  "$SCRIPT"

UNBOUND_PLAN_SPECS_ROOT="$TMP_DIR/unbound_plan/specifications"
UNBOUND_PLAN_PLANS_ROOT="$TMP_DIR/unbound_plan/implementation-plans"
write_bound_spec "$UNBOUND_PLAN_SPECS_ROOT/bound.md"
write_unbound_plan "$UNBOUND_PLAN_PLANS_ROOT/unbound.md"
run_failure missing_plan_workflow_binding env \
  HARNESS_WORKFLOW_MANIFEST="$MANIFEST" \
  HARNESS_WORKFLOW_DOC="$DOC" \
  HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC" \
  HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS" \
  HARNESS_WORKFLOW_SPECS_ROOT="$UNBOUND_PLAN_SPECS_ROOT" \
  HARNESS_WORKFLOW_PLANS_ROOT="$UNBOUND_PLAN_PLANS_ROOT" \
  HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_plan_binding" \
  "$SCRIPT"

printf 'harness workflow validator tests passed\n'
