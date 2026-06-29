# Harness Workflows

This project has two harness layers:

1. **Release harness:** hard, tool-neutral checks in `scripts/verify_release.sh`.
2. **Task workflow harness:** a documented execution shape for choosing when to use one agent, many agents, worktrees, adversarial verification, loops, task ledgers, agent-legible runtime surfaces, skills, or quarantined readers.

Dynamic task workflows can improve broad or adversarial AI work, but they do not replace the release harness. The final prerelease authority remains `scripts/verify_release.sh`.

## Source Traceability

The workflow catalog is derived from official Claude Code, Anthropic, OpenAI, and Codex sources. The traceable source and principle matrix lives in `docs/harness-source-analysis.md`.

Every workflow class in `docs/harness-workflows.json` must declare:

- `source_ids`: official sources that justify the workflow.
- `principle_ids`: adopted principles from the source analysis.

The validator checks those IDs so this project does not drift into undocumented agent folklore.

## Core Patterns

| Pattern | Meaning |
| --- | --- |
| `classifier-routing` | Use a classifier step to choose workflow class, model, agent, or next action. |
| `fanout-barrier-synthesis` | Split work into parallel slices, wait for all results, then synthesize. |
| `adversarial-verification` | Verify outputs with a separate agent or pass against a rubric. |
| `generate-filter` | Generate multiple candidates, filter against constraints, then keep only reviewed winners. |
| `tournament-selection` | Let multiple alternatives compete through pairwise or rubric-based judgment. |
| `loop-until-done` | Repeat until a stop condition is met, not for a fixed number of passes. |
| `quarantine` | Separate agents that read untrusted content from agents that perform privileged actions. |
| `model-routing` | Choose model intelligence based on expected complexity and tool-call shape. |
| `worktree-isolation` | Run broad or risky slices in isolated git worktrees before synthesis. |
| `token-budget` | Declare budget and parallelism before increasing compute. |
| `resumable-evidence` | Write durable artifacts so interrupted work can be resumed or audited. |
| `source-traceability` | Preserve source IDs, source URLs, and principle IDs for workflow design choices. |
| `progressive-disclosure` | Keep entry instructions small and load references, scripts, and details only when needed. |
| `cache-safe-prefix` | Preserve stable instructions, tool surfaces, and append-only context where possible. |
| `task-graph` | Track long work as an explicit graph or ledger of tasks, blockers, owners, and evidence. |
| `artifact-review` | Produce dense review artifacts, including HTML when useful, for human and verifier inspection. |
| `agent-legibility` | Make app state, repo maps, logs, metrics, traces, and UI state easy for agents to inspect. |
| `runtime-feedback` | Let agents run the system, interact with it, and inspect feedback from the real runtime. |
| `trajectory-review` | Review the agent's action sequence and decisions, not only the final diff. |
| `eval-improvement-loop` | Convert repeated failures into evals, tests, scripts, hooks, or skill improvements. |
| `mechanical-invariants` | Encode repeated correctness rules in scripts, linters, tests, or CI gates. |
| `sandbox-boundary` | Keep autonomy inside explicit filesystem, network, command, approval, and credential boundaries. |
| `hook-gate` | Use lifecycle hooks or equivalent mechanical checks for repeated safety or quality gates. |
| `context-reset` | Use fresh sessions, compaction checkpoints, or handoff notes to avoid stale context. |
| `skill-packaging` | Turn repeated workflows into focused skills with triggers, references, scripts, and tests. |
| `human-escalation` | Route product, safety, credential, or irreversible decisions to a human instead of hiding assumptions. |

## Workflow Classes

### HARNESS-FOCUSED-CHANGE

Use for narrow changes that fit in one context window. Keep execution linear, run focused verification, then run the release harness.

### HARNESS-SPEC-FIRST-FEATURE

Use when a request changes behavior, public contracts, jobs, schemas, prompts, or business logic. Write or update the spec first, plan from the spec, implement, review, and prove readiness with release evidence.

### HARNESS-WIDE-REFACTOR

Use for broad call-site, module, package, or naming changes. Split the refactor into slices, run isolated worktrees when possible, review each slice, then synthesize and run the release harness.

### HARNESS-DEEP-VERIFICATION

Use when claims must be checked before publication or release. Extract claims, verify each claim independently, review source quality separately, and remove or mark unsupported claims.

### HARNESS-RESEARCH-SYNTHESIS

Use for broad source-backed research. Fan out evidence collection, verify source quality, synthesize with citations or local file references, and preserve uncertainty instead of collapsing contradictions.

### HARNESS-SECURITY-REVIEW

Use for security review, threat modeling, exploit validation, or fix review. Separate discovery, exploitability analysis, validation, and remediation review. Quarantine untrusted inputs and escalate suspected production secrets or active exploits.

### HARNESS-INCIDENT-TRIAGE

Use for logs, tickets, alerts, recurring root causes, or incident streams. Classify, dedupe, investigate, and escalate with bounded evidence. Use loops only with explicit stop conditions.

### HARNESS-EXPLORATION-TOURNAMENT

Use for naming, design direction, architecture alternatives, or strategy options. Generate alternatives, judge them against a rubric, and record why the winner beat close competitors.

### HARNESS-LONG-RUN-TASK-GRAPH

Use for multi-session, multi-agent, or long-running work where context can rot or partial completion can hide. Maintain a task graph or ledger with status, evidence, blockers, next action, and clean handoff notes.

### HARNESS-RUNTIME-LEGIBILITY

Use when changing development environments, app startup, observability, UI feedback, logs, traces, metrics, local smoke paths, or other surfaces that make the project legible to agents. The workflow proves that an agent can start, inspect, and validate the running system.

### HARNESS-EVAL-IMPROVEMENT-LOOP

Use when repeated agent mistakes, prompt failures, flaky workflows, or review escapes need to become durable tests, evals, hooks, scripts, or checklist changes. Review trajectories, capture examples, improve the gate, and rerun the verifier.

### HARNESS-SKILL-EVOLUTION

Use when a repeated workflow should become a reusable skill, plugin, reference, or deterministic script. Keep the skill focused, use progressive disclosure, test trigger behavior, and avoid turning the entry point into an encyclopedia.

## Choosing A Workflow

Start with `HARNESS-FOCUSED-CHANGE`. Escalate only when the task has one of these properties:

- Many independent slices can run in parallel.
- The task is long enough that one context may hide partial completion.
- The result needs independent verification.
- The input includes untrusted public or production content.
- The amount of work is unknown and needs a loop with a stop condition.
- The answer depends on qualitative ranking or competing alternatives.
- Model cost or intelligence should be routed by complexity.
- The runtime, UI, logs, metrics, traces, or local environment must become easier for agents to inspect.
- Repeated agent failures should become evals, scripts, hooks, or skills.
- The work needs a durable task graph, compaction checkpoint, or handoff ledger.

## Evidence Contract

Each workflow class in `docs/harness-workflows.json` declares required evidence paths. Evidence should live under `.artifacts/` and may include:

- release summaries from `scripts/verify_release.sh`
- per-slice logs
- task graphs or handoff ledgers
- claim verification tables
- review rubrics
- source lists
- runtime smoke logs, screenshots, traces, or metrics snapshots
- trajectory review notes
- eval or hook regression output
- skill trigger tests
- synthesis notes
- unresolved blocker lists

Chat summaries are useful context, but they are not release evidence.

Every non-template markdown specification under `docs/specifications/` and every implementation plan under `docs/implementation-plans/` must include a `Workflow Class: HARNESS-*` binding. `docs/harness-virtual-requirements.json` remains the regression set for expected workflow routing. This makes the workflow choice part of new-demand development instead of an optional chat convention.

## Guardrails

- Approval-required paths still require owner approval.
- Forbidden paths stay forbidden.
- Workflow scripts and AI-tool wrappers must not become the only enforcement point.
- A workflow that consumes untrusted content must not directly perform privileged writes.
- A workflow that uses extra parallelism must declare budget and stop conditions first.
- Source traceability supports but does not replace current-state verification.
- Cache-safe context practices must never override sandbox, approval, credential, or release-gate boundaries.
- Rich artifacts are review aids; they are not a substitute for spec, tests, and release evidence.
