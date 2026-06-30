# 2026-06-29 World Cup Agent Effect Guardrails Specification

- Spec ID: `SPEC-WORLDCUP-AGENT-EFFECT-GUARDRAILS-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: the Lark product document "世界杯预测Agent对话模块 产品定义文档" defines the Agent chat module as the WC2026 prediction Agent's right-side explanation panel and asks that its actual capability guardrails be implemented as the effect-tuning framework.
- Target baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server` after `SPEC-WORLDCUP-CHAT-SERVER-MIGRATION-001`.
- Current behavior:
  - `app/runtime/chat_behavior.py` has generic World Cup forecasting identity, high-confidence secret/prompt/money/account guardrails, language consistency, and output leak protection.
  - `tests/chat_eval/golden_cases.jsonl` covers generic evidence-led forecasting, EV, no-bet, language, and secret/real-money safety.
  - `scripts/sample_knowledge.json` contains minimal World Cup method and safety notes.
- Problem:
  - The current policy is still too broad. Product requires the Agent to be the model's explanation layer, not a football pundit, betting advisor, platform support agent, or paywall bypass.
  - Product sections 1, 2, and 3 define the Agent role, positive answer topics, and negative/refusal topics. Section 2 positive examples depend on centralized match-data interfaces that are still pending, so this slice must encode the answer framework and no-fabrication behavior before tool integration.
  - Product sections 4, 5, and 6 add concrete answer-style, unlocked-content, data-scope, risk-prompt, and ideal-vs-bad-answer rules that are not yet represented in prompt policy, deterministic guardrails, golden cases, or seed knowledge.
- Non-goals:
  - No central match-data API integration in this slice; teammates are developing those interfaces separately.
  - No API, DB, event, queue, auth, or schema changes.
  - No real-money order placement, Polymarket execution, or account/wallet access.
  - No browser-session storage implementation in this backend slice.

## PRD Audit Summary

- covered:
  - Product positioning is explicit: the module is an "Agent model explainer" for the current match's model pipeline.
  - Allowed topics are explicit: recommendation trigger logic, strength index calculation, model probability calculation, and model-working-principle explanations.
  - Refusal or handoff topics are explicit: direct betting decisions, guaranteed outcomes, out-of-model football/news questions, platform/account support, and paywall bypass.
  - Style requirements are explicit: follow user/page language, concise but numeric, explanatory tone, no emotional betting language, and a first-token experience target.
  - Examples define what good and bad answers look like for direct-buy, accuracy, and no-recommendation questions.
- missing:
  - Exact backend data contract field names for current match, unlock state, model probability, hidden probability, odds, first-lineup refresh, and recommendation summary.
  - Exact UI transport for typing animation and <=1.5s first-character measurement.
- conflicts:
  - Existing policy currently allows broad "World Cup match forecasting" wording; product narrows the role to model-output explanation for the current match.
  - Existing policy mentions web search as a tool; product forbids live lookup of injuries, transfers, public opinion, or other dimensions outside the model input set.
- assumptions:
  - Until central APIs arrive, the backend can enforce policy, guardrails, answer templates, evals, and seed knowledge without adding data fields.
  - Section 2 exact-value answers should be framed as "query centralized data, then fill the template"; if centralized data or tool fields are absent, the Agent explains the required fields and method without inventing values.
  - Direct betting decision prompts should not be answered as "buy/sell"; the safe path is to refuse the decision while offering model-result explanation when data is available.
  - Locked paid values are represented as product/content policy, not auth enforcement in this slice.
- recommended PRD additions:
  - Provide the central data response schema and unlock-state semantics.
  - Provide the exact frontend event or prop contract for typing animation and first-character latency measurement.
  - Provide the authoritative model limitations text from PRD section 8.3 for the knowledge base.
- harness impact:
  - `HARNESS-SPEC-FIRST-FEATURE` because runtime behavior and test fixtures change.
  - Focused tests must prove prompt policy, input guardrails, output guardrails, golden cases, and deterministic answer traits.
  - Release readiness remains `make verify-release`.
- go/no-go:
  - Go for this slice under accepted assumptions; central data integration remains a future spec.

## Product Semantics

- User/operator workflow:
  - A user asks about the currently selected WC2026 match in the Agent side panel.
  - The Agent explains the current match's model output: strength index, expected goals, probability, recommendation trigger, and no-recommendation reason.
  - For Section 2 positive examples, the Agent first identifies the answer class:
    - recommendation logic: compare model probability with Polymarket implied probability, require at least a 4 percentage-point gap, and require odds in the 1.70-2.40 range.
    - strength index: explain 9 independent dimensions, weighted 0-100 score, opponent Elo/SOS correction, weight table use, and lineup-triggered recalculation.
    - model probability: explain expected-goals `λ`, Poisson grid, WDL aggregation, `k=0.943`, `ρ=-0.15`, and stage calibration.
    - model principle: explain the PRD 8.1 loose-grid model and the Agent's model-explainer role.
  - The Agent refuses to make the user's final betting decision and redirects to model explanation.
  - On match switch, frontend may send a system-style prompt or use future central data; the Agent response style must support a concise active message with match name, home win rate, expected goals, recommendation summary, and "请问您想了解什么？".
- State model:
  - No persisted state changes.
  - Browser-session-only chat history is a product/UI statement; backend persistence remains unchanged for this service until a separate auth/session spec changes it.
- Ownership and identity rules:
  - The Agent speaks as the model-output explainer.
  - It does not speak as a betting platform, account support agent, football pundit, sportsbook, or guarantee provider.
- Permissions/authentication:
  - No auth contract change.
  - The Agent must not reveal locked block B model-probability values or block D recommendation values when the prompt states the user is not unlocked.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - Existing chat validation and run semantics stay unchanged.
  - Deterministic input refusals remain successful assistant answers and bypass model/tool execution.
  - Output guardrail replacements remain safe terminal content.
  - If WC2026 central agent-data API is not configured, current-match tools fail closed with sanitized `central_unavailable` content and must not fabricate current-match probabilities, odds, strength scores, recommendations, or paid values.
  - Public methodology may use a local fallback containing only stable public rules and constants, such as 4pp trigger threshold, 1.70-2.40 odds range, 9D/0-100 strength-index framework, `k=0.943`, `rho=-0.15`, and stage-calibration semantics.
- Compatibility and migration expectations:
  - Existing clients, routes, events, RAG admin behavior, and conversation APIs remain compatible.
  - Existing World Cup behavior cases remain valid unless narrowed by product guardrails.

## API / Interface Contract

- Routes, commands, events, jobs, or UI surfaces:
  - No route, command, event, job, or UI API change.
- Request fields and validation:
  - No new request fields.
  - Future central-data fields are out of scope.
- Response/envelope fields and types:
  - No envelope change.
  - Allowed answers should use concise, numeric, explanation-first prose when centralized data is available.
  - If centralized data or a future tool result is not available, allowed answers must state the missing fields and explain the calculation or business rule without fabricating `X/Y/Z`, win rates, `λ`, odds, weights, recommendation direction, or paid values.
- Status/error codes:
  - No status-code change.
  - Policy refusals are assistant content, not HTTP errors.
- Pagination/sorting/filtering:
  - Not applicable.
- Backward compatibility:
  - `/chat`, `/stream`, `/ws`, `/runs`, `/conversations`, `/rag/*`, `/healthz`, `/readyz`, and `/metrics` stay unchanged.

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - None.
- Read models, projections, snapshots, caches:
  - None.
- Rebuild or cleanup operators:
  - None.
- Historical data behavior:
  - Historical runs retain older policy versions.
- Performance-sensitive queries or write paths:
  - No new DB query.
  - Deterministic guardrail checks stay local string/pattern checks.
  - <=1.5s first-character response is recorded as a product/UI target, not implemented in this backend-only slice.

## Architecture

- Modules/files expected to change:
  - `app/runtime/chat_behavior.py`: policy version, identity, product-effect guardrails, output guardrails, and safe responses.
  - `app/runtime/orchestrator.py`: raw model text tracking for output-integrity checks when streamed display text is compacted.
  - `app/runtime/wc2026_agent_data.py`: central-data service construction, sanitized current-match fail-closed behavior when no base URL is configured, and local public-methodology fallback.
  - `dockerhost/compose.yaml` and `scripts/check_dockerhost_production_config.py`: provider default alignment for API and worker services.
  - `tests/test_chat_behavior_policy.py`: focused policy and guardrail tests.
  - `tests/chat_eval/golden_cases.jsonl`: product examples and effect-tuning oracles.
  - `tests/chat_eval/evaluator.py` and `tests/test_chat_behavior_eval.py`: coverage counters for new guardrail categories.
  - `tests/chat_eval/judge.py`: deterministic answer variants for new golden cases.
  - `tests/test_agent_factory.py`: prompt identity/product positioning assertions.
  - `scripts/sample_knowledge.json`: stable product/model explanation knowledge entries.
  - This specification and matching implementation plan.
- Data flow:
  - Same as existing chat behavior policy: API persists user message, orchestrator evaluates input guardrail, safe refusals short-circuit, allowed runs proceed through Pydantic AI and tools, output guardrail protects final and streamed content.
  - The streaming output guardrail enforces default side-panel style before `TOKEN` events are emitted, while the orchestrator keeps raw model text for truncation detection so style compaction cannot mask provider cutoffs.
- Transaction/concurrency boundaries:
  - No new transaction or lock.
- Observability/logging/metrics:
  - Existing guardrail plan metadata records category/reason without sensitive data.
  - No raw private product data, tokens, or provider secrets are logged.
- Rollback strategy:
  - Revert policy, tests, sample knowledge, spec, and plan as one patch.
  - Existing API/schema state needs no rollback.

## Harness Classification

- Expected gate(s):
  - `HARNESS-SPEC-FIRST-FEATURE`
  - `scripts/check_ai_boundaries.sh`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
  - Focused pytest and `make verify-release`
- Performance-sensitive class:
  - Runtime hot path is touched only by local string checks.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Focused tests prove guardrail additions remain deterministic and do not require external services.
  - Release gate proves import/test compatibility.
- Focused verification commands:
  - `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_chat_behavior_eval.py tests/test_agent_factory.py -q`
  - `.venv/bin/python -m pytest tests/test_orchestrator.py -q`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
- Prerelease-grade verification commands:
  - `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - System prompt identifies the Agent as the WC2026 model-output explainer, not a pundit, betting advisor, or platform support agent.
  - Prompt treats centralized match data as the only source for current-match numeric values, and instructs the Agent to explain missing fields instead of inventing numbers when tools are not available.
  - Prompt includes recommendation trigger rules: model probability must exceed Polymarket implied probability by at least 4 percentage points and odds must be in the 1.70-2.40 range before a recommendation can be explained.
  - Prompt includes Section 2 answer frameworks for recommendation reason, odds/no-recommendation reason, strength-index calculation, weight rationale, lineup repricing, expected-goals/win-rate values, and model-principle coefficients.
  - Prompt includes strength-index, expected-goals/probability, model-principle, and style constraints from the product document.
  - Prompt enforces concise answer style: conclusion first, 3-5 short bullets by default, and no default Markdown tables or long section scaffolding unless the user explicitly asks for detail.
  - Streaming output converts default Markdown table rows into plain short lines, removes table/horizontal-rule scaffolding, and clamps verbose default answers without weakening hidden-instruction, language, direct-betting, guaranteed-profit, or truncation guardrails.
  - Input guardrail refuses direct betting-decision prompts, guaranteed-outcome prompts, locked-content bypass prompts, model-scope-out-of-bounds prompts, and platform/account support prompts.
  - Output guardrail blocks direct buy/sell advice and guaranteed-profit/outcome language.
  - Allowed market, recommendation, EV, odds, or Polymarket explanations receive a deterministic risk footer when the model omitted one.
  - Long model answers that look cut off before a terminal sentence are treated as `TRUNCATED_OUTPUT` failures rather than successful final answers.
  - Live answer-effect evaluation flags verbose answers and default Markdown tables as deterministic style failures.
  - DockerHost API and worker services keep `ZAI_REASONING_EFFORT` and provider max-output-token defaults aligned, and production-config verification fails on drift.
- Edge cases:
  - "这个模型准不准？" remains allowed but must answer with model limitations rather than a fixed accuracy claim.
  - "为什么没有推荐投注？" remains allowed and must cite trigger conditions.
  - Safe documentation questions about API keys or prompt concepts remain allowed.
  - Direct order execution and personal Polymarket account data remain refused as before.
- Compatibility:
  - Existing guardrail categories and golden cases continue to pass.
  - No public API, schema, route, event, or status-code change.
- Operational:
  - No real provider secret, product private value, or locked paid numeric value is committed in tests or docs.
  - Release evidence is written under `.artifacts/release` by the existing harness.
- Evidence artifacts:
  - New specification and implementation plan.
  - Focused red/green pytest output, including Section 2 central-data-pending golden cases.
  - Release harness summary.

## Review Notes

- Open questions:
  - Centralized data interface fields and unlock semantics are pending from teammates.
  - Frontend timing contract for typing animation and first-character SLA is pending.
- Accepted assumptions:
  - Backend policy can enforce product wording and deterministic local guardrails before central data integration.
  - Product examples may be represented as golden cases with placeholder `X/Y/Z` values or missing-field language until real data is available.
- Rejected alternatives:
  - Do not add central-data schemas from product text alone.
  - Do not rely only on prompt copy without golden cases and output guardrails.
  - Do not loosen real-money safety to satisfy "recommendation" wording.
- Reviewer findings and resolution:
  - Pending implementation review.
