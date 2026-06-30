# 2026-06-29 World Cup Centralized Agent Data Contract Implementation Plan

- Specification: `SPEC-WORLDCUP-CENTRALIZED-AGENT-DATA-CONTRACT-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`
- Target branch/baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`
- Scope summary: define and later implement the read-only centralized match-data contract required by the WC2026 Agent model-explainer answers.
- Out of scope: central-service implementation, DB migrations, account/wallet access, betting execution, frontend typing animation, and platform support flows.

## Change Steps

1. Contract review with product/backend
   - Files/modules: `docs/specifications/2026-06-29-worldcup-centralized-agent-data-contract-specification.md`.
   - Behavior change: no runtime change; align on aggregate snapshot shape, unlock masking, missing-field semantics, and Section 2 answer requirements.
   - Data contract impact: defines the proposed central API/tool response.
   - Tests to add/update: none in this review-only step.
   - Verification command: `scripts/check_spec_contract.sh`.
   - Rollback or compatibility note: doc-only change; no runtime rollback.

2. Mock tool adapter
   - Files/modules: future tool adapter under the runtime/tool layer, plus test fixtures.
   - Behavior change: allow Agent tests to fill recommendation, strength-index, probability, market, lineup, and risk templates from mock `WorldcupAgentMatchContext`.
   - Data contract impact: fixture must conform to `schema_version: "2026-06-29"`.
   - Tests to add/update: complete, partial, locked, stale, no-recommendation, and lineup-reprice fixtures.
   - Verification command: focused pytest for tool adapter and chat behavior evals.
   - Rollback or compatibility note: mock adapter can be disabled without API/schema changes.

3. Real central adapter integration
   - Files/modules: future read-only adapter that calls the teammate-owned central service.
   - Behavior change: replace mock data with real match snapshot while preserving the same Agent-facing schema.
   - Data contract impact: no Agent prompt change if the central service matches the aggregate contract.
   - Tests to add/update: contract tests against sanitized real or recorded responses.
   - Verification command: focused adapter tests, chat behavior evals, and `make verify-release`.
   - Rollback or compatibility note: fall back to mock/no-data mode and missing-field answers if the central service is unavailable.

4. Answer-level integration cases
   - Files/modules: `tests/chat_eval/golden_cases.jsonl`, deterministic judge fixtures, future integration fixtures.
   - Behavior change: prove that complete snapshots produce filled numeric answers and partial/locked snapshots produce missing-field or masked answers.
   - Data contract impact: requires stable sample payloads for each Section 2 product example.
   - Tests to add/update: recommendation reason, odds-out-of-window, no recommendation, strength index, weight rationale, expected goals, WDL probability, k/rho, stage calibration, lineup reprice.
   - Verification command: focused chat eval pytest.
   - Rollback or compatibility note: golden cases are additive and can be narrowed if product changes.

5. Harness verification and release readiness
   - Files/modules: docs, future adapter, tests, fixtures.
   - Behavior change: no further behavior change; verify contract and implementation gates.
   - Data contract impact: central schema changes require spec update first.
   - Tests to add/update: none unless review finds gaps.
   - Verification command:
     - `scripts/check_spec_contract.sh`
     - `scripts/check_harness_workflows.sh`
     - `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`
   - Rollback or compatibility note: required gates must pass before claiming integration readiness.

## Risk Controls

- Public contract risks: schema versioning is explicit; field additions should be backward-compatible.
- Money/accounting/security risks: contract is read-only; no execution, wallet, account, deposit, or withdrawal fields.
- Paid-content risks: locked Block B/D exact values should be omitted or masked before reaching the Agent.
- Migration/rebuild risks: none for the Chat Server until real adapter work begins.
- Performance risks: aggregate query should target p95 under 800 ms to preserve first-character budget.
- Deployment/test-branch risks: no deployment required for contract review.
- Unrelated local changes to avoid: do not alter runtime behavior, DB schema, API routes, queue workers, or provider limits while only negotiating the central contract.

## Completion Criteria

- Product/backend agree on the aggregate tool name, request shape, response shape, unlock masking semantics, and missing-field semantics.
- Mock adapter can generate payloads for all Section 2 positive examples.
- Real adapter returns the same Agent-facing schema or a versioned compatible schema.
- Focused tests cover complete, partial, locked, stale, and no-recommendation states.
- Spec-contract, Harness workflow, and release gate pass before production use.
