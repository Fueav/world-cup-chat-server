# 2026-06-29 World Cup Agent Effect Guardrails Implementation Plan

- Specification: `SPEC-WORLDCUP-AGENT-EFFECT-GUARDRAILS-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`
- Scope summary: convert the Lark product document's Agent-effect guardrails into versioned runtime policy, deterministic guardrails, central-data-ready answer frameworks, golden cases, answer-level judge fixtures, and seed knowledge.
- Out of scope: central match-data API integration, schema/API changes, UI typing animation implementation, real Polymarket execution, and account/platform support workflows.

## Change Steps

1. Test-first product guardrails
   - Files/modules: `tests/test_chat_behavior_policy.py`, `tests/test_agent_factory.py`, `tests/test_chat_behavior_eval.py`, `tests/chat_eval/golden_cases.jsonl`.
   - Behavior change: encode product positioning, section 2 positive answer frameworks, section 3 refusals, section 5 business boundaries, and section 6 answer examples as failing tests and golden fixtures.
   - Data contract impact: none.
   - Tests to add/update: prompt assertions, input guardrail tests, output guardrail tests, golden coverage assertions.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_chat_behavior_eval.py tests/test_agent_factory.py -q`.
   - Rollback or compatibility note: tests are additive and can be reverted with the policy patch.

2. Runtime behavior policy and deterministic guardrails
   - Files/modules: `app/runtime/chat_behavior.py`.
   - Behavior change: bump policy to v4, narrow role to model-output explainer, add central-data-only numeric answer policy, add product-effect categories, refuse direct betting decisions, guaranteed outcomes, locked-content bypass, model-scope-out-of-bounds, and platform/account support; block unsafe output advice/guarantees.
   - Data contract impact: run-plan guardrail metadata may carry new category values.
   - Tests to add/update: focused policy and orchestrator-compatible tests.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_orchestrator.py -q`.
   - Rollback or compatibility note: no DB rollback; historical v3 plans remain valid.

3. Answer-level deterministic judge and golden cases
   - Files/modules: `tests/chat_eval/evaluator.py`, `tests/chat_eval/judge.py`, `tests/test_chat_behavior_eval.py`, `tests/chat_eval/golden_cases.jsonl`.
   - Behavior change: cover allowed section 2 central-data-pending questions for recommendation reason, odds/no-recommendation, strength index, weights, lineup repricing, expected goals, expected win rate, coefficients, and stage calibration; also cover refused direct/guaranteed/locked/out-of-scope/platform prompts.
   - Data contract impact: none.
   - Tests to add/update: golden schema, coverage, allowed-case judge.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_behavior_eval.py -q`.
   - Rollback or compatibility note: deterministic fixtures avoid external judge/provider dependencies.

4. Seed knowledge update
   - Files/modules: `scripts/sample_knowledge.json`.
   - Behavior change: add stable model-explainer, style, trigger-condition, section 2 answer-framework, central-data dependency, model-limitation, and data-scope knowledge.
   - Data contract impact: seed content only.
   - Tests to add/update: existing RAG tests should continue to pass.
   - Verification command: `.venv/bin/python -m pytest tests/test_rag_contracts.py tests/test_rag_agent_tool.py -q`.
   - Rollback or compatibility note: no migration; content can be reseeded by operators if needed.

5. Harness verification and review
   - Files/modules: docs, tests, runtime policy, sample knowledge.
   - Behavior change: no further behavior change; verify spec/plan alignment and release gates.
   - Data contract impact: none.
   - Tests to add/update: no additional tests unless review finds a gap.
   - Verification command:
     - `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_chat_behavior_eval.py tests/test_agent_factory.py tests/test_orchestrator.py -q`
     - `scripts/check_spec_contract.sh`
     - `scripts/check_harness_workflows.sh`
     - `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`
   - Rollback or compatibility note: `AI_BOUNDARY_APPROVED=1` acknowledges the user-requested runtime policy edit; failures must be fixed, not bypassed.

6. Post-live-eval answer completeness hardening
   - Files/modules: `app/runtime/chat_behavior.py`, `app/runtime/orchestrator.py`, `tests/live_eval/`.
   - Behavior change: append a deterministic risk footer for allowed market/recommendation/EV answers when the model omits one; classify likely provider cutoffs as `TRUNCATED_OUTPUT` instead of persisting a partial successful answer; make live eval catch suspected truncation and accept compact "九维" wording.
   - Data contract impact: no schema change; truncated realtime runs converge to failed status with sanitized error metadata.
   - Tests to add/update: focused policy finalizer tests, orchestrator truncation test, live eval scorer fixture tests.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_orchestrator.py tests/live_eval/test_wc2026_effect_cases.py -q`.
   - Rollback or compatibility note: revert this step if provider behavior changes and the truncation heuristic causes unacceptable false positives.

7. Concise answer style hardening before live re-evaluation
   - Files/modules: `app/runtime/chat_behavior.py`, `app/runtime/orchestrator.py`, `app/runtime/wc2026_agent_data.py`, `dockerhost/compose.yaml`, `scripts/check_dockerhost_production_config.py`, `tests/test_chat_behavior_policy.py`, `tests/test_wc2026_agent_data.py`, `tests/live_eval/`.
   - Behavior change: instruct the model to answer conclusion-first with 3-5 short bullets by default, avoid Markdown tables unless explicitly requested, filter default Markdown tables/horizontal rules before streaming, clamp default verbose generated output while preserving raw-output truncation detection, expose missing central-data base URL as client-unavailable instead of a synthetic transport failure, and keep DockerHost API/worker provider defaults aligned.
   - Data contract impact: no external schema change; unconfigured central-data service still fails closed with a sanitized `central_unavailable` tool result.
   - Tests to add/update: prompt assertion, streaming style guardrail tests, live-eval scorer style checks, central-data unconfigured-service test, and DockerHost production-config check.
   - Verification command: `.venv/bin/python -m pytest tests/test_chat_behavior_policy.py tests/test_wc2026_agent_data.py tests/test_production_readiness.py tests/live_eval/test_wc2026_effect_cases.py tests/test_agent_factory.py tests/test_orchestrator.py -q`.
   - Rollback or compatibility note: if a future product flow requires long explanations, add explicit per-case `max_answer_chars` or an opt-in detailed-answer mode rather than weakening the default side-panel style; central-data fail-closed behavior must not be relaxed to fabricate match-specific values.

## Risk Controls

- Public contract risks: no route, field, schema, event, or status-code changes.
- Money/accounting/security risks: direct betting decisions and guaranteed-profit claims are tightened, not loosened; no execution workflow is added.
- Migration/rebuild risks: none.
- Performance risks: only local string checks added to guardrail path.
- Deployment/test-branch risks: live evaluation uses a disposable DockerHost environment and must record the tested commit and target URL.
- Unrelated local changes to avoid: do not touch DB migrations, DockerHost deployment scripts, or unrelated docs.

## Completion Criteria

- New specification and implementation plan validate.
- Tests are written and observed failing before runtime policy implementation.
- Focused behavior tests pass.
- Spec-contract, Harness workflow, and release gate pass.
- Code review findings are fixed or explicitly accepted.
- Residual central-data/API and UI timing gaps are documented as out of scope.
