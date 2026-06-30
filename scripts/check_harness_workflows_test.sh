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
| `progressive-disclosure` | Load details only when needed. |
| `sandbox-boundary` | Keep autonomy inside explicit boundaries. |

## Agent Team Suitability

Agent Team decisions are recorded with workflow evidence.

## Workflow Classes

### HARNESS-FOCUSED-CHANGE

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
      "patterns": ["token-budget", "resumable-evidence", "progressive-disclosure", "sandbox-boundary"],
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
      "agent_team": {
        "suitability": "avoid",
        "rationale": "Focused changes do not benefit from extra coordination.",
        "use_when": ["Keep one lead agent for small, obvious changes."],
        "avoid_when": ["Avoid Agent Team when there is no independent slice or reviewer role."],
        "coordination": "single-lead-agent-with-focused-verification",
        "evidence": ["Record the Agent Team decision when a focused change is intentionally kept single-agent."]
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
  cat >"$file" <<JSON
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

write_harness_spec_dir() {
  local dir="$1"
  mkdir -p "$dir"
  cat >"$dir/harness.md" <<'SPEC'
# Harness Binding

Spec ID: `SPEC-HARNESS-WORKFLOW-001`

Workflow Class: `HARNESS-FOCUSED-CHANGE`
SPEC
  cat >"$dir/invariants.md" <<'SPEC'
# Harness Workflow Invariants

Spec ID: `SPEC-HARNESS-WORKFLOW-001`

Workflow Class: `HARNESS-FOCUSED-CHANGE`

- Workflow classes declare stop conditions.
SPEC
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
    printf 'PASS %s
' "$name"
  else
    printf 'expected success for %s
' "$name" >&2
    cat "$TMP_DIR/$name.out" >&2 || true
    cat "$TMP_DIR/$name.err" >&2 || true
    exit 1
  fi
}

run_failure() {
  local name="$1"
  shift
  if "$@" >"$TMP_DIR/$name.out" 2>"$TMP_DIR/$name.err"; then
    printf 'expected failure for %s
' "$name" >&2
    cat "$TMP_DIR/$name.out" >&2 || true
    exit 1
  fi
  printf 'PASS %s
' "$name"
}

DOC="$TMP_DIR/docs/harness-workflows.md"
SOURCE_DOC="$TMP_DIR/docs/harness-source-analysis.md"
MANIFEST="$TMP_DIR/docs/harness-workflows.json"
VIRTUAL_REQUIREMENTS="$TMP_DIR/docs/harness-virtual-requirements.json"
SPECS_ROOT="$TMP_DIR/docs/specifications"
HARNESS_SPEC_DIR="$SPECS_ROOT/harness_workflows"
PLANS_ROOT="$TMP_DIR/docs/implementation-plans"

write_doc "$DOC"
write_source_doc "$SOURCE_DOC"
write_valid_manifest "$MANIFEST"
write_virtual_requirements "$VIRTUAL_REQUIREMENTS"
write_harness_spec_dir "$HARNESS_SPEC_DIR"
write_bound_spec "$SPECS_ROOT/bound.md"
write_bound_plan "$PLANS_ROOT/bound.md"

run_success valid_manifest env   HARNESS_WORKFLOW_MANIFEST="$MANIFEST"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/valid"   "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/missing_stop.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0].pop("stop_conditions")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_stop_conditions env   HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/missing_stop.json"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_stop"   "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/missing_agent_team.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0].pop("agent_team")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_agent_team env   HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/missing_agent_team.json"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_agent_team"   "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/focused_recommends_agent_team.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0]["agent_team"]["suitability"] = "recommended"
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure focused_recommends_agent_team env   HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/focused_recommends_agent_team.json"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/focused_recommends_agent_team"   "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/unknown_pattern.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["workflow_classes"][0]["patterns"].append("unknown-pattern")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_pattern env   HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/unknown_pattern.json"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_pattern"   "$SCRIPT"

python3 - "$MANIFEST" "$TMP_DIR/missing_source_set.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data.pop("source_set")
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure missing_source_set env   HARNESS_WORKFLOW_MANIFEST="$TMP_DIR/missing_source_set.json"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_source_set"   "$SCRIPT"

python3 - "$VIRTUAL_REQUIREMENTS" "$TMP_DIR/unknown_virtual_workflow.json" <<'PY'
import json
import sys
source, target = sys.argv[1], sys.argv[2]
data = json.load(open(source, encoding="utf-8"))
data["cases"][0]["expected_workflow"] = "HARNESS-NOT-REAL"
json.dump(data, open(target, "w", encoding="utf-8"))
PY
run_failure unknown_virtual_workflow env   HARNESS_WORKFLOW_MANIFEST="$MANIFEST"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$TMP_DIR/unknown_virtual_workflow.json"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/unknown_virtual_workflow"   "$SCRIPT"

write_unbound_spec "$SPECS_ROOT/unbound.md"
run_failure missing_spec_binding env   HARNESS_WORKFLOW_MANIFEST="$MANIFEST"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_spec_binding"   "$SCRIPT"

rm "$SPECS_ROOT/unbound.md"
write_unbound_plan "$PLANS_ROOT/unbound.md"
run_failure missing_plan_binding env   HARNESS_WORKFLOW_MANIFEST="$MANIFEST"   HARNESS_WORKFLOW_DOC="$DOC"   HARNESS_WORKFLOW_SOURCE_DOC="$SOURCE_DOC"   HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS="$VIRTUAL_REQUIREMENTS"   HARNESS_WORKFLOW_SPEC_DIR="$HARNESS_SPEC_DIR"   HARNESS_WORKFLOW_SPECS_ROOT="$SPECS_ROOT"   HARNESS_WORKFLOW_PLANS_ROOT="$PLANS_ROOT"   HARNESS_WORKFLOW_ARTIFACT_DIR="$TMP_DIR/artifacts/missing_plan_binding"   "$SCRIPT"
