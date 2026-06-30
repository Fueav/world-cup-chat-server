# Harness Source Analysis

This file records the official Claude Code, Anthropic, OpenAI, and Codex sources that shape the project Harness contract. It exists so future agents can update the workflow system from traceable sources instead of memory or chat.

## Official Source Set

| Source ID | Provider | Source |
| --- | --- | --- |
| `openai-harness-engineering` | OpenAI | https://openai.com/index/harness-engineering/ |
| `openai-codex-agent-loop` | OpenAI | https://openai.com/index/unrolling-the-codex-agent-loop/ |
| `openai-codex-manual` | OpenAI | https://developers.openai.com/codex/codex-manual.md |
| `openai-codex-maxxing` | OpenAI | https://openai.com/index/codex-maxxing-long-running-work/ |
| `openai-codex-safety-governance` | OpenAI | https://openai.com/index/running-codex-safely/ |
| `openai-codex-windows-sandbox` | OpenAI | https://openai.com/index/building-a-safe-effective-sandbox-for-codex-on-windows/ |
| `openai-agent-computer-environment` | OpenAI | https://openai.com/index/from-model-to-agent-equipping-the-responses-api-with-a-computer-environment/ |
| `openai-agentic-websockets` | OpenAI | https://openai.com/index/speeding-up-agentic-workflows-with-websockets-in-the-responses-api/ |
| `openai-prompt-injection` | OpenAI | https://openai.com/index/designing-agents-to-resist-prompt-injection/ |
| `openai-agent-misalignment-monitoring` | OpenAI | https://openai.com/index/how-we-monitor-internal-coding-agents-misalignment/ |
| `claude-dynamic-workflows` | Anthropic | https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code |
| `claude-long-running-agents` | Anthropic | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents |
| `claude-long-running-apps` | Anthropic | https://www.anthropic.com/engineering/harness-design-long-running-apps |
| `claude-multi-agent-research` | Anthropic | https://www.anthropic.com/engineering/multi-agent-research-system |
| `claude-skills` | Anthropic | https://claude.com/blog/lessons-from-building-claude-code-how-we-use-skills |
| `claude-prompt-caching` | Anthropic | https://claude.com/blog/lessons-from-building-claude-code-prompt-caching-is-everything |
| `claude-html-artifacts` | Anthropic | https://claude.com/blog/using-claude-code-the-unreasonable-effectiveness-of-html |
| `claude-code-best-practices` | Anthropic | https://code.claude.com/docs/en/best-practices |
| `claude-auto-mode` | Anthropic | https://www.anthropic.com/engineering/claude-code-auto-mode |
| `claude-sandboxing` | Anthropic | https://www.anthropic.com/engineering/claude-code-sandboxing |
| `claude-managed-agents` | Anthropic | https://www.anthropic.com/engineering/managed-agents |
| `claude-context-engineering` | Anthropic | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| `claude-tool-design` | Anthropic | https://www.anthropic.com/engineering/writing-tools-for-agents |
| `claude-code-execution-mcp` | Anthropic | https://www.anthropic.com/engineering/code-execution-with-mcp |
| `claude-infrastructure-noise` | Anthropic | https://www.anthropic.com/engineering/infrastructure-noise |
| `claude-agent-evals` | Anthropic | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents |

## Adopted Principles

| Principle ID | Project meaning | Main sources |
| --- | --- | --- |
| `source-traceability` | Harness classes and principles must point back to official sources. | `openai-harness-engineering`, `claude-dynamic-workflows` |
| `release-gate-hard-authority` | Dynamic workflows guide execution, but `scripts/verify_release.sh` stays the release authority. | `openai-codex-manual`, `openai-harness-engineering` |
| `map-not-encyclopedia` | Keep root agent guidance short and use docs/specs as the indexed system of record. | `openai-harness-engineering`, `openai-codex-manual` |
| `agent-legible-environment` | Agents need direct access to app state, logs, metrics, traces, UI snapshots, and deterministic run commands. | `openai-harness-engineering`, `openai-codex-safety-governance`, `claude-long-running-agents`, `claude-long-running-apps`, `claude-managed-agents` |
| `incremental-task-ledger` | Long work should progress one feature or task at a time with structured state for the next session. | `claude-long-running-agents`, `claude-long-running-apps`, `openai-codex-maxxing` |
| `external-evaluator-loop` | The agent doing the work should not be the only judge of done. | `claude-dynamic-workflows`, `claude-long-running-apps`, `openai-codex-manual`, `claude-agent-evals` |
| `agent-team-suitability` | Agent teams are recommended when work decomposes into independent slices or verification roles, and skipped when coordination overhead exceeds value. | `openai-harness-engineering`, `claude-dynamic-workflows`, `claude-multi-agent-research`, `claude-managed-agents` |
| `cache-safe-context` | Stable prefixes, stable tool sets, append-only updates, and cache-safe compaction keep long workflows efficient. | `claude-prompt-caching`, `openai-codex-agent-loop`, `openai-codex-maxxing`, `claude-context-engineering` |
| `skill-progressive-disclosure` | Repeated workflows become focused skills with trigger descriptions, references, scripts, gotchas, and tests. | `claude-skills`, `openai-codex-manual`, `claude-code-best-practices` |
| `sandbox-and-quarantine` | Untrusted readers, privileged actors, sandbox boundaries, and hooks are separate safety layers. | `claude-dynamic-workflows`, `openai-codex-manual`, `openai-codex-safety-governance`, `openai-codex-windows-sandbox`, `openai-prompt-injection`, `claude-auto-mode`, `claude-sandboxing` |
| `continuous-garbage-collection` | Agent-first repos need mechanical principles and recurring cleanup so drift does not compound. | `openai-harness-engineering`, `claude-skills`, `claude-infrastructure-noise` |
| `artifact-review-surface` | Dense review artifacts, including HTML when useful, help humans and verifier agents inspect complex work. | `claude-html-artifacts`, `claude-long-running-apps` |
| `autonomy-governance` | Higher autonomy requires explicit permission classifiers, sandbox constraints, credential boundaries, telemetry, and human escalation paths. | `openai-codex-safety-governance`, `openai-codex-windows-sandbox`, `openai-prompt-injection`, `openai-agent-misalignment-monitoring`, `claude-auto-mode`, `claude-sandboxing` |
| `session-interface-decoupling` | Long-running or remote agents should separate reasoning, execution environment, transport, state persistence, and replayable session control. | `openai-agent-computer-environment`, `openai-agentic-websockets`, `claude-managed-agents` |
| `tool-context-economy` | Tools, MCP servers, and code-execution surfaces should expose compact, task-shaped context instead of forcing agents to parse noisy raw state. | `openai-agent-computer-environment`, `claude-code-best-practices`, `claude-context-engineering`, `claude-tool-design`, `claude-code-execution-mcp` |
| `eval-infrastructure-calibration` | Agent evals must separate model behavior from infrastructure noise, flaky harnesses, and evaluator bias before they drive product or gate decisions. | `openai-codex-agent-loop`, `openai-agent-misalignment-monitoring`, `claude-infrastructure-noise`, `claude-agent-evals` |
| `operational-telemetry` | Autonomous agent systems need auditable event streams, risk signals, pause/kill paths, and owner-visible state transitions. | `openai-codex-safety-governance`, `openai-agent-misalignment-monitoring`, `openai-harness-engineering`, `claude-managed-agents` |

## Gap Audit

| Area | Previous state | Project response |
| --- | --- | --- |
| Source traceability | Workflow classes existed but did not carry source IDs or adopted principles. | `docs/harness-workflows.json` now requires `source_set`, `adopted_principles`, `source_ids`, and `principle_ids`. |
| Dynamic workflow patterns | Core classifier, fan-out, adversarial, loop, quarantine, model routing, tournament, budget, and worktree patterns existed. | Pattern catalog now also covers generate-filter, progressive disclosure, source tracing, task ledgers, runtime feedback, cache-safe prefix, trajectory review, hook gates, skill packaging, and artifact review. |
| Agent Team selection | Workflows named parallelism but did not make the use-or-skip judgment explicit. | Every workflow class now declares Agent Team suitability, recommended use cases, avoid cases, coordination shape, and decision evidence. |
| Long-running handoff | Existing classes had resumable evidence but no workflow for multi-session task ledgers. | `HARNESS-LONG-RUN-TASK-GRAPH` covers feature ledgers, clean handoffs, context reset or compaction points, and session boot checks. |
| Agent-legible runtime | Release evidence existed, but runtime/UI/log/metric legibility was not a first-class workflow. | `HARNESS-RUNTIME-LEGIBILITY` covers one-command boot, app-driving smoke tests, logs, metrics, traces, and worktree-local runtime evidence. |
| Eval improvement | Adversarial review existed, but repeated agent failures did not have a loop for turning examples into evals or hooks. | `HARNESS-EVAL-IMPROVEMENT-LOOP` covers trajectory review, regression examples, evals, deterministic scripts, and verifier reruns. |
| Skill evolution | `.agents/skills` existed, but repeated workflows were not connected to skill creation and measurement. | `HARNESS-SKILL-EVOLUTION` covers gotchas, references, scripts, trigger descriptions, hook gates, and measured skill behavior. |
| Cache-sensitive agent design | No explicit rule protected stable prompts/tool surfaces. | `cache-safe-prefix` is now a documented pattern and principle for long workflows, skills, and compaction-sensitive runs. |
| Human review surface | Markdown docs were the default review artifact. | `artifact-review` allows richer visual or HTML review surfaces when complexity justifies them, while release evidence remains tool-neutral. |
| Autonomy governance | Security review covered findings, but permission classifiers, automatic modes, sandbox policy, credential scope, remote sessions, and pause/kill paths were not a first-class task class. | `HARNESS-AUTONOMY-GOVERNANCE` requires permission classification, sandbox and credential evidence, telemetry, operator controls, and independent risk review before autonomy increases. |
| Remote session interface | Runtime legibility covered local boot and inspection, but not separation of reasoning, execution, transport, session state, and replay for managed or long-lived agents. | `session-interface` and `session-interface-decoupling` now cover managed-agent, computer-use, and WebSocket-style agent sessions. |
| Tool context economy | Skills existed, but tool and MCP design did not explicitly optimize for compact, task-shaped context. | `tool-context-economy` now binds skill evolution and runtime legibility to context-engineering and tool-design sources. |
| Eval noise calibration | Eval loops existed, but infrastructure noise and evaluator bias were not separately named. | `eval-noise-calibration` requires distinguishing model behavior from harness flake, infra noise, and evaluator bias before changing gates. |

## Cooperation Rules

- Claude-specific workflow names, Codex-specific surfaces, and local AI-tool features are source material, not project authority. Project authority stays in `AGENTS.md`, `docs/specifications/`, `docs/implementation-plans/`, `docs/`, scripts, and `.ai-boundaries.yml`.
- Dynamic workflows should increase compute only when their class explains the budget, isolation model, and stop condition.
- Agent Teams are a suitability decision, not a ceremony. Use them for independent slices, breadth-first exploration, adversarial verification, tournaments, or long-running task graphs; skip them for tight, sequential, or low-risk work where coordination overhead would dominate.
- Source traceability does not replace current-state verification. Every implementation still has to pass the release harness.
- Cache efficiency must not weaken safety. Stable tool sets, append-only context updates, and compaction hygiene are performance practices; sandbox boundaries and approval-required paths still apply.
- HTML or rich artifacts are optional review surfaces, not mandatory release artifacts. They are useful when a reviewer needs comparison, interaction, visualization, or dense cross-source inspection.
- Skills should stay small and triggerable. If a workflow grows into a broad encyclopedia, move details into references and keep the entry point concise.
