# Harness Source Analysis

This file records the official Claude Code, Anthropic, OpenAI, and Codex sources that shape the project Harness contract. It exists so future agents can update the workflow system from traceable sources instead of memory or chat.

## Official Source Set

| Source ID | Provider | Source |
| --- | --- | --- |
| `openai-harness-engineering` | OpenAI | https://openai.com/index/harness-engineering/ |
| `openai-codex-agent-loop` | OpenAI | https://openai.com/index/unrolling-the-codex-agent-loop/ |
| `openai-codex-manual` | OpenAI | https://developers.openai.com/codex/codex-manual.md |
| `claude-dynamic-workflows` | Anthropic | https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code |
| `claude-long-running-agents` | Anthropic | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents |
| `claude-long-running-apps` | Anthropic | https://www.anthropic.com/engineering/harness-design-long-running-apps |
| `claude-skills` | Anthropic | https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills |
| `claude-prompt-caching` | Anthropic | https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything |
| `claude-html-artifacts` | Anthropic | https://claude.com/blog/using-claude-code-the-unreasonable-effectiveness-of-html |

## Adopted Principles

| Principle ID | Project meaning | Main sources |
| --- | --- | --- |
| `source-traceability` | Harness classes and principles must point back to official sources. | `openai-harness-engineering`, `claude-dynamic-workflows` |
| `release-gate-hard-authority` | Dynamic workflows guide execution, but `scripts/verify_release.sh` stays the release authority. | `openai-codex-manual`, `openai-harness-engineering` |
| `map-not-encyclopedia` | Keep root agent guidance short and use docs/specs as the indexed system of record. | `openai-harness-engineering`, `openai-codex-manual` |
| `agent-legible-environment` | Agents need direct access to app state, logs, metrics, traces, UI snapshots, and deterministic run commands. | `openai-harness-engineering`, `claude-long-running-agents`, `claude-long-running-apps` |
| `incremental-task-ledger` | Long work should progress one feature or task at a time with structured state for the next session. | `claude-long-running-agents`, `claude-long-running-apps` |
| `external-evaluator-loop` | The agent doing the work should not be the only judge of done. | `claude-dynamic-workflows`, `claude-long-running-apps`, `openai-codex-manual` |
| `cache-safe-context` | Stable prefixes, stable tool sets, append-only updates, and cache-safe compaction keep long workflows efficient. | `claude-prompt-caching`, `openai-codex-agent-loop` |
| `skill-progressive-disclosure` | Repeated workflows become focused skills with trigger descriptions, references, scripts, gotchas, and tests. | `claude-skills`, `openai-codex-manual` |
| `sandbox-and-quarantine` | Untrusted readers, privileged actors, sandbox boundaries, and hooks are separate safety layers. | `claude-dynamic-workflows`, `openai-codex-manual` |
| `continuous-garbage-collection` | Agent-first repos need mechanical principles and recurring cleanup so drift does not compound. | `openai-harness-engineering`, `claude-skills` |
| `artifact-review-surface` | Dense review artifacts, including HTML when useful, help humans and verifier agents inspect complex work. | `claude-html-artifacts`, `claude-long-running-apps` |

## Gap Audit

| Area | Previous state | Project response |
| --- | --- | --- |
| Source traceability | Workflow classes existed but did not carry source IDs or adopted principles. | `docs/harness-workflows.json` now requires `source_set`, `adopted_principles`, `source_ids`, and `principle_ids`. |
| Dynamic workflow patterns | Core classifier, fan-out, adversarial, loop, quarantine, model routing, tournament, budget, and worktree patterns existed. | Pattern catalog now also covers generate-filter, progressive disclosure, source tracing, task ledgers, runtime feedback, cache-safe prefix, trajectory review, hook gates, skill packaging, and artifact review. |
| Long-running handoff | Existing classes had resumable evidence but no workflow for multi-session task ledgers. | `HARNESS-LONG-RUN-TASK-GRAPH` covers feature ledgers, clean handoffs, context reset or compaction points, and session boot checks. |
| Agent-legible runtime | Release evidence existed, but runtime/UI/log/metric legibility was not a first-class workflow. | `HARNESS-RUNTIME-LEGIBILITY` covers one-command boot, app-driving smoke tests, logs, metrics, traces, and worktree-local runtime evidence. |
| Eval improvement | Adversarial review existed, but repeated agent failures did not have a loop for turning examples into evals or hooks. | `HARNESS-EVAL-IMPROVEMENT-LOOP` covers trajectory review, regression examples, evals, deterministic scripts, and verifier reruns. |
| Skill evolution | `.agents/skills` existed, but repeated workflows were not connected to skill creation and measurement. | `HARNESS-SKILL-EVOLUTION` covers gotchas, references, scripts, trigger descriptions, hook gates, and measured skill behavior. |
| Cache-sensitive agent design | No explicit rule protected stable prompts/tool surfaces. | `cache-safe-prefix` is now a documented pattern and principle for long workflows, skills, and compaction-sensitive runs. |
| Human review surface | Markdown docs were the default review artifact. | `artifact-review` allows richer visual or HTML review surfaces when complexity justifies them, while release evidence remains tool-neutral. |

## Cooperation Rules

- Claude-specific workflow names, Codex-specific surfaces, and local AI-tool features are source material, not project authority. Project authority stays in `AGENTS.md`, `docs/specifications/`, `docs/implementation-plans/`, `docs/`, scripts, and `.ai-boundaries.yml`.
- Dynamic workflows should increase compute only when their class explains the budget, isolation model, and stop condition.
- Source traceability does not replace current-state verification. Every implementation still has to pass the release harness.
- Cache efficiency must not weaken safety. Stable tool sets, append-only context updates, and compaction hygiene are performance practices; sandbox boundaries and approval-required paths still apply.
- HTML or rich artifacts are optional review surfaces, not mandatory release artifacts. They are useful when a reviewer needs comparison, interaction, visualization, or dense cross-source inspection.
- Skills should stay small and triggerable. If a workflow grows into a broad encyclopedia, move details into references and keep the entry point concise.
