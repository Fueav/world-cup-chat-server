#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON:-${PY:-python3}}"

MANIFEST="${HARNESS_WORKFLOW_MANIFEST:-$ROOT_DIR/docs/harness-workflows.json}"
DOC="${HARNESS_WORKFLOW_DOC:-$ROOT_DIR/docs/harness-workflows.md}"
SOURCE_DOC="${HARNESS_WORKFLOW_SOURCE_DOC:-$ROOT_DIR/docs/harness-source-analysis.md}"
VIRTUAL_REQUIREMENTS="${HARNESS_WORKFLOW_VIRTUAL_REQUIREMENTS:-$ROOT_DIR/docs/harness-virtual-requirements.json}"
SPEC_DIR="${HARNESS_WORKFLOW_SPEC_DIR:-$ROOT_DIR/docs/specifications/harness_workflows}"
SPECS_ROOT="${HARNESS_WORKFLOW_SPECS_ROOT:-$ROOT_DIR/docs/specifications}"
PLANS_ROOT="${HARNESS_WORKFLOW_PLANS_ROOT:-$ROOT_DIR/docs/implementation-plans}"
ARTIFACT_DIR="${HARNESS_WORKFLOW_ARTIFACT_DIR:-${VERIFY_ARTIFACT_DIR:-$ROOT_DIR/.artifacts/release}}"

mkdir -p "$ARTIFACT_DIR"

"$PYTHON_BIN" - "$MANIFEST" "$DOC" "$SOURCE_DOC" "$VIRTUAL_REQUIREMENTS" "$SPEC_DIR" "$SPECS_ROOT" "$PLANS_ROOT" "$ARTIFACT_DIR" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

manifest_path = Path(sys.argv[1])
doc_path = Path(sys.argv[2])
source_doc_path = Path(sys.argv[3])
virtual_requirements_path = Path(sys.argv[4])
spec_dir = Path(sys.argv[5])
specs_root = Path(sys.argv[6])
plans_root = Path(sys.argv[7])
artifact_dir = Path(sys.argv[8])

allowed_patterns = set([
  "adversarial-verification",
  "agent-legibility",
  "agent-native-telemetry",
  "artifact-review",
  "cache-safe-prefix",
  "classifier-routing",
  "context-reset",
  "credential-vaulting",
  "eval-improvement-loop",
  "eval-noise-calibration",
  "fanout-barrier-synthesis",
  "generate-filter",
  "hook-gate",
  "human-escalation",
  "loop-until-done",
  "mechanical-invariants",
  "model-routing",
  "permission-classifier",
  "progressive-disclosure",
  "quarantine",
  "resumable-evidence",
  "runtime-feedback",
  "sandbox-boundary",
  "session-interface",
  "skill-packaging",
  "source-traceability",
  "task-graph",
  "token-budget",
  "tool-context-economy",
  "tournament-selection",
  "trajectory-review",
  "worktree-isolation"
])
require_strategies = False
allowed_worktree = {"none", "optional", "required"}
allowed_agent_team_suitability = {"avoid", "optional", "recommended"}
allowed_source_providers = {"OpenAI", "Anthropic"}
allowed_source_statuses = {"adopted", "reference"}
allowed_source_url_prefixes = (
    "https://openai.com/",
    "https://developers.openai.com/",
    "https://platform.openai.com/",
    "https://www.anthropic.com/",
    "https://anthropic.com/",
    "https://claude.com/",
    "https://code.claude.com/",
)
allowed_required_strategies = {"context_strategy", "tool_policy", "state_strategy", "artifact_strategy"}
spec_id = "SPEC-HARNESS-WORKFLOW-001"
binding_re = re.compile(r"Workflow Class:\s*`?(HARNESS-[A-Z0-9-]+)`?")
kebab_re = re.compile(r"[a-z0-9][a-z0-9-]*")
errors: list[str] = []


def add_error(message: str) -> None:
    errors.append(message)


def non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def non_empty_list(value: object) -> bool:
    return isinstance(value, list) and bool(value)


def require_file(path: Path, label: str) -> bool:
    if not path.is_file():
        add_error(f"{label} not found: {path}")
        return False
    return True


def load_json(path: Path, label: str) -> dict[str, Any]:
    if not require_file(path, label):
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add_error(f"{label} is invalid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        add_error(f"{label} must be a JSON object")
        return {}
    return payload


def load_text(path: Path, label: str) -> str:
    if not require_file(path, label):
        return ""
    return path.read_text(encoding="utf-8")


def require_string(obj: dict[str, Any], key: str, context: str) -> None:
    if not non_empty_string(obj.get(key)):
        add_error(f"{context}.{key} must be a non-empty string")


def require_list(obj: dict[str, Any], key: str, context: str) -> list[Any]:
    value = obj.get(key)
    if not non_empty_list(value):
        add_error(f"{context}.{key} must be a non-empty array")
        return []
    return value


def markdown_files(root: Path, label: str) -> list[Path]:
    if not root.exists():
        add_error(f"{label} root not found: {root}")
        return []
    return sorted(path for path in root.rglob("*.md") if "_template" not in path.parts)


def validate_bindings(root: Path, label: str, workflow_ids: set[str]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for path in markdown_files(root, label):
        text = path.read_text(encoding="utf-8")
        match = binding_re.search(text)
        rel = path.as_posix()
        if not match:
            add_error(f"{label} missing Workflow Class binding: {rel}")
            continue
        workflow_id = match.group(1)
        bindings[rel] = workflow_id
        if workflow_id not in workflow_ids:
            add_error(f"{label} uses unknown Workflow Class {workflow_id}: {rel}")
    return bindings


def validate_strategy_fields(workflow: dict[str, Any], workflow_id: str) -> None:
    context_strategy = workflow.get("context_strategy")
    if not isinstance(context_strategy, dict):
        add_error(f"{workflow_id} requires context_strategy object")
        context_strategy = {}
    for key in ("session_boundary", "context_rot", "cache_policy", "subagent_policy"):
        if not non_empty_string(context_strategy.get(key)):
            add_error(f"{workflow_id} context_strategy.{key} must be non-empty")

    tool_policy = workflow.get("tool_policy")
    if not isinstance(tool_policy, dict):
        add_error(f"{workflow_id} requires tool_policy object")
        tool_policy = {}
    if not non_empty_string(tool_policy.get("surface")):
        add_error(f"{workflow_id} tool_policy.surface must be non-empty")
    if not isinstance(tool_policy.get("progressive_disclosure"), bool):
        add_error(f"{workflow_id} tool_policy.progressive_disclosure must be boolean")
    if tool_policy.get("tool_mutation") not in {"not-applicable", "stable-prefix-or-deferred-loading"}:
        add_error(f"{workflow_id} tool_policy.tool_mutation must be not-applicable or stable-prefix-or-deferred-loading")

    state_strategy = workflow.get("state_strategy")
    if not isinstance(state_strategy, dict):
        add_error(f"{workflow_id} requires state_strategy object")
        state_strategy = {}
    if state_strategy.get("task_graph") not in {"none", "optional", "required"}:
        add_error(f"{workflow_id} state_strategy.task_graph must be none, optional, or required")
    if not isinstance(state_strategy.get("dependencies"), bool):
        add_error(f"{workflow_id} state_strategy.dependencies must be boolean")
    if not isinstance(state_strategy.get("resume"), bool):
        add_error(f"{workflow_id} state_strategy.resume must be boolean")

    artifact_strategy = workflow.get("artifact_strategy")
    if not isinstance(artifact_strategy, dict):
        add_error(f"{workflow_id} requires artifact_strategy object")
        artifact_strategy = {}
    if artifact_strategy.get("human_review") not in {"none", "markdown", "html", "interactive-html"}:
        add_error(f"{workflow_id} artifact_strategy.human_review must be none, markdown, html, or interactive-html")
    if artifact_strategy.get("export") not in {"none", "markdown", "json", "prompt", "diff", "html"}:
        add_error(f"{workflow_id} artifact_strategy.export must be none, markdown, json, prompt, diff, or html")
    if not non_empty_string(artifact_strategy.get("evidence")):
        add_error(f"{workflow_id} artifact_strategy.evidence must be non-empty")


doc_text = load_text(doc_path, "workflow document")
source_doc_text = load_text(source_doc_path, "workflow source analysis")
manifest = load_json(manifest_path, "workflow manifest")

if manifest.get("version") != 1:
    add_error("workflow manifest version must be 1")

source_set = manifest.get("source_set")
source_ids: set[str] = set()
source_urls: dict[str, str] = {}
source_statuses: dict[str, str] = {}
if not non_empty_list(source_set):
    add_error("workflow manifest must include non-empty source_set")
    source_set = []

for index, source in enumerate(source_set):
    context = f"source_set[{index}]"
    if not isinstance(source, dict):
        add_error(f"{context} must be an object")
        continue
    source_id = source.get("id")
    if not non_empty_string(source_id) or kebab_re.fullmatch(str(source_id)) is None:
        add_error(f"{context}.id must be a kebab-case string")
        continue
    if str(source_id) in source_ids:
        add_error(f"duplicate source id: {source_id}")
    source_ids.add(str(source_id))
    for key in ("title", "url"):
        require_string(source, key, context)
    provider = source.get("provider")
    if provider not in allowed_source_providers:
        add_error(f"{context}.provider must be one of {sorted(allowed_source_providers)}")
    status = source.get("status")
    if status not in allowed_source_statuses:
        add_error(f"{context}.status must be one of {sorted(allowed_source_statuses)}")
    else:
        source_statuses[str(source_id)] = str(status)
    source_url = source.get("url")
    if non_empty_string(source_url):
        source_urls[str(source_id)] = str(source_url)
        if not str(source_url).startswith(allowed_source_url_prefixes):
            add_error(f"{context}.url must use an official OpenAI, Anthropic, or Claude domain: {source_url}")
    if source_doc_text and str(source_id) not in source_doc_text:
        add_error(f"{source_id} is not documented in {source_doc_path}")
    if source_doc_text and non_empty_string(source_url) and str(source_url) not in source_doc_text:
        add_error(f"{source_id} URL is not documented in {source_doc_path}: {source_url}")

principles = manifest.get("adopted_principles")
principle_ids: set[str] = set()
principle_source_bindings: dict[str, list[str]] = {}
if not non_empty_list(principles):
    add_error("workflow manifest must include non-empty adopted_principles")
    principles = []

for index, principle in enumerate(principles):
    context = f"adopted_principles[{index}]"
    if not isinstance(principle, dict):
        add_error(f"{context} must be an object")
        continue
    principle_id = principle.get("id")
    if not non_empty_string(principle_id) or kebab_re.fullmatch(str(principle_id)) is None:
        add_error(f"{context}.id must be a kebab-case string")
        continue
    if str(principle_id) in principle_ids:
        add_error(f"duplicate principle id: {principle_id}")
    principle_ids.add(str(principle_id))
    require_string(principle, "summary", context)
    principle_sources = [str(item) for item in require_list(principle, "source_ids", context)]
    for source_id in principle_sources:
        if source_id not in source_ids:
            add_error(f"{context}.source_ids contains unknown source id: {source_id}")
    principle_source_bindings[str(principle_id)] = principle_sources
    if source_doc_text and str(principle_id) not in source_doc_text:
        add_error(f"{principle_id} is not documented in {source_doc_path}")

classes = manifest.get("workflow_classes")
workflow_ids: list[str] = []
workflow_by_id: dict[str, dict[str, Any]] = {}
workflow_source_bindings: dict[str, list[str]] = {}
workflow_principle_bindings: dict[str, list[str]] = {}
workflow_agent_team_bindings: dict[str, str] = {}
if not non_empty_list(classes):
    add_error("workflow manifest must include non-empty workflow_classes")
    classes = []

for index, workflow in enumerate(classes):
    context = f"workflow_classes[{index}]"
    if not isinstance(workflow, dict):
        add_error(f"{context} must be an object")
        continue
    workflow_id_value = workflow.get("id")
    if not non_empty_string(workflow_id_value) or not str(workflow_id_value).startswith("HARNESS-"):
        add_error(f"{context}.id must be a HARNESS-* string")
        workflow_id = f"<missing-{index}>"
    elif str(workflow_id_value) in workflow_by_id:
        add_error(f"duplicate workflow id: {workflow_id_value}")
        workflow_id = str(workflow_id_value)
    else:
        workflow_id = str(workflow_id_value)
        workflow_ids.append(workflow_id)
        workflow_by_id[workflow_id] = workflow
        if doc_text and workflow_id not in doc_text:
            add_error(f"{workflow_id} is not documented in {doc_path}")

    for key in ("name", "purpose"):
        require_string(workflow, key, context)
    for key in ("source_ids", "principle_ids", "use_when", "patterns", "stop_conditions", "evidence", "human_escalation"):
        require_list(workflow, key, context)

    workflow_sources = [str(item) for item in workflow.get("source_ids", [])]
    for source_id in workflow_sources:
        if source_id not in source_ids:
            add_error(f"{workflow_id} references unknown source id: {source_id}")
    workflow_source_bindings[workflow_id] = workflow_sources

    workflow_principles = [str(item) for item in workflow.get("principle_ids", [])]
    for principle_id in workflow_principles:
        if principle_id not in principle_ids:
            add_error(f"{workflow_id} references unknown principle id: {principle_id}")
    workflow_principle_bindings[workflow_id] = workflow_principles

    patterns = [str(item) for item in workflow.get("patterns", [])]
    for pattern in patterns:
        if pattern not in allowed_patterns:
            add_error(f"{workflow_id} uses unknown pattern: {pattern}")
        if doc_text and pattern not in doc_text:
            add_error(f"{workflow_id} uses undocumented pattern: {pattern}")

    isolation = workflow.get("isolation")
    if not isinstance(isolation, dict):
        add_error(f"{workflow_id} requires isolation object")
        isolation = {}
    if isolation.get("worktree") not in allowed_worktree:
        add_error(f"{workflow_id} isolation.worktree must be one of {sorted(allowed_worktree)}")
    if not non_empty_string(isolation.get("context")):
        add_error(f"{workflow_id} isolation.context must be non-empty")
    if not isinstance(isolation.get("quarantine_untrusted_inputs"), bool):
        add_error(f"{workflow_id} isolation.quarantine_untrusted_inputs must be boolean")
    if isolation.get("quarantine_untrusted_inputs") and "quarantine" not in patterns:
        add_error(f"{workflow_id} quarantines inputs but does not declare the quarantine pattern")

    verification = workflow.get("verification")
    if not isinstance(verification, dict):
        add_error(f"{workflow_id} requires verification object")
        verification = {}
    primary_command = verification.get("primary_command")
    if not non_empty_string(primary_command):
        add_error(f"{workflow_id} verification.primary_command must be non-empty")
    elif any(ai_tool in str(primary_command).lower() for ai_tool in (".claude", ".codex", "claude ", "codex ")):
        add_error(f"{workflow_id} verification.primary_command must be tool-neutral: {primary_command}")
    if not isinstance(verification.get("adversarial_review"), bool):
        add_error(f"{workflow_id} verification.adversarial_review must be boolean")
    if not non_empty_list(verification.get("rubric")):
        add_error(f"{workflow_id} verification.rubric must be non-empty")

    for evidence_path in workflow.get("evidence", []):
        if not isinstance(evidence_path, str) or not evidence_path.startswith(".artifacts/"):
            add_error(f"{workflow_id} evidence path must live under .artifacts/: {evidence_path}")

    budget = workflow.get("budget")
    if not isinstance(budget, dict):
        add_error(f"{workflow_id} requires budget object")
        budget = {}
    require_string(budget, "token_budget", f"{workflow_id}.budget")
    require_string(budget, "parallelism", f"{workflow_id}.budget")

    agent_team = workflow.get("agent_team")
    if not isinstance(agent_team, dict):
        add_error(f"{workflow_id} requires agent_team object")
    else:
        suitability = agent_team.get("suitability")
        if suitability not in allowed_agent_team_suitability:
            add_error(f"{workflow_id} agent_team.suitability must be one of {sorted(allowed_agent_team_suitability)}")
        for key in ("rationale", "coordination"):
            require_string(agent_team, key, f"{workflow_id}.agent_team")
        for key in ("use_when", "avoid_when", "evidence"):
            values = require_list(agent_team, key, f"{workflow_id}.agent_team")
            for value in values:
                if not non_empty_string(value):
                    add_error(f"{workflow_id} agent_team.{key} must contain only non-empty strings")
        evidence_text = " ".join(str(item) for item in agent_team.get("evidence", [])).lower()
        if "decision" not in evidence_text:
            add_error(f"{workflow_id} agent_team.evidence must mention recording the Agent Team decision")
        if suitability != "avoid" and "synthesis" not in evidence_text:
            add_error(f"{workflow_id} agent_team.evidence must mention synthesis when Agent Team use is possible")
        if workflow_id == "HARNESS-FOCUSED-CHANGE" and suitability != "avoid":
            add_error("HARNESS-FOCUSED-CHANGE agent_team.suitability must be avoid")
        if suitability == "avoid" and (
            "fanout-barrier-synthesis" in patterns
            or "tournament-selection" in patterns
            or isolation.get("worktree") == "required"
        ):
            add_error(f"{workflow_id} agent_team.suitability cannot be avoid for fan-out, tournament, or required-worktree workflows")
        if isinstance(suitability, str):
            workflow_agent_team_bindings[workflow_id] = suitability

    if require_strategies:
        validate_strategy_fields(workflow, workflow_id)

workflow_id_set = set(workflow_ids)
if workflow_ids and "HARNESS-FOCUSED-CHANGE" not in workflow_id_set:
    add_error("workflow manifest must include HARNESS-FOCUSED-CHANGE as the default class")

referenced_source_ids: set[str] = set()
for source_list in principle_source_bindings.values():
    referenced_source_ids.update(source_list)
for source_list in workflow_source_bindings.values():
    referenced_source_ids.update(source_list)
for source_id, status in source_statuses.items():
    if status == "adopted" and source_id not in referenced_source_ids:
        add_error(f"adopted source is not bound to any principle or workflow: {source_id}")

if not spec_dir.is_dir():
    add_error(f"workflow spec dir does not exist: {spec_dir}")
else:
    spec_files = [path for path in spec_dir.iterdir() if path.is_file()]
    if not spec_files:
        add_error(f"workflow spec dir has no files: {spec_dir}")
    elif not any(spec_id in path.read_text(encoding="utf-8") for path in spec_files):
        add_error(f"workflow spec files must reference {spec_id}")
    harness_file = spec_dir / "harness.md"
    if not harness_file.is_file():
        add_error(f"workflow spec dir must include harness.md: {spec_dir}")
    elif not binding_re.search(harness_file.read_text(encoding="utf-8")):
        add_error("workflow spec harness.md must declare Workflow Class: `HARNESS-*`")

spec_bindings = validate_bindings(specs_root, "specification", workflow_id_set)
plan_bindings = validate_bindings(plans_root, "implementation plan", workflow_id_set)

virtual_requirements = load_json(virtual_requirements_path, "virtual requirements")
virtual_cases: dict[str, str] = {}
if virtual_requirements.get("version") != 1:
    add_error("virtual requirements version must be 1")

cases = virtual_requirements.get("cases")
if not non_empty_list(cases):
    add_error("virtual requirements require a non-empty cases list")
    cases = []

for index, case in enumerate(cases):
    context = f"virtual_requirements.cases[{index}]"
    if not isinstance(case, dict):
        add_error(f"{context} must be an object")
        continue
    for key in ("id", "request", "expected_workflow", "rationale"):
        require_string(case, key, context)
    case_id = str(case.get("id", f"<missing-{index}>"))
    expected = str(case.get("expected_workflow", ""))
    if expected:
        virtual_cases[case_id] = expected
    workflow = workflow_by_id.get(expected)
    if workflow is None:
        add_error(f"{context} references unknown expected_workflow: {expected}")
        continue
    required_patterns = [str(item) for item in require_list(case, "required_patterns", context)]
    workflow_patterns = set(str(item) for item in workflow.get("patterns", []))
    for pattern in required_patterns:
        if pattern not in allowed_patterns:
            add_error(f"{context} requires unknown pattern {pattern}")
        if pattern not in workflow_patterns:
            add_error(f"{context} requires pattern {pattern} but {expected} does not declare it")
    if require_strategies:
        required_strategies = [str(item) for item in require_list(case, "required_strategies", context)]
        for strategy in required_strategies:
            if strategy not in allowed_required_strategies:
                add_error(f"{context} has unknown required strategy: {strategy}")
            elif not isinstance(workflow.get(strategy), dict):
                add_error(f"{context} requires {strategy} but {expected} does not declare it")

artifact_dir.mkdir(parents=True, exist_ok=True)
summary = {
    "status": "failed" if errors else "passed",
    "manifest": str(manifest_path),
    "document": str(doc_path),
    "source_document": str(source_doc_path),
    "virtual_requirements": str(virtual_requirements_path),
    "workflow_spec_dir": str(spec_dir),
    "source_count": len(source_ids),
    "source_ids": sorted(source_ids),
    "source_urls": source_urls,
    "source_statuses": source_statuses,
    "principle_count": len(principle_ids),
    "principle_ids": sorted(principle_ids),
    "principle_source_bindings": principle_source_bindings,
    "workflow_count": len(workflow_ids),
    "workflow_ids": workflow_ids,
    "workflow_source_bindings": workflow_source_bindings,
    "workflow_principle_bindings": workflow_principle_bindings,
    "workflow_agent_team_bindings": workflow_agent_team_bindings,
    "allowed_patterns": sorted(allowed_patterns),
    "spec_bindings": spec_bindings,
    "plan_bindings": plan_bindings,
    "virtual_cases": virtual_cases,
    "errors": errors,
}
(artifact_dir / "harness_workflows.json").write_text(
    json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    encoding="utf-8",
)

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    sys.exit(1)

print(
    "harness workflow validation passed: "
    f"{len(workflow_ids)} workflows, "
    f"{len(source_ids)} sources, "
    f"{len(principle_ids)} principles, "
    f"{len(spec_bindings)} specs, "
    f"{len(plan_bindings)} plans, "
    f"{len(virtual_cases)} virtual cases"
)
PY
