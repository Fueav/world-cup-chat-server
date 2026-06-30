# 2026-06-29 World Cup Centralized Agent Data Contract Specification

- Spec ID: `SPEC-WORLDCUP-CENTRALIZED-AGENT-DATA-CONTRACT-001`
- Workflow Class: `HARNESS-SPEC-FIRST-FEATURE`

## Context

- PRD/source request: the Lark product document "世界杯预测Agent对话模块 产品定义文档" defines the Agent as the WC2026 model-output explainer. Section 2 positive examples require centralized match data for recommendation reasons, strength index values, model probabilities, expected goals, and model-parameter explanations.
- Target baseline: `main` in `/Users/chris/AiProject/world-cup-chat-server`, after `SPEC-WORLDCUP-AGENT-EFFECT-GUARDRAILS-001`.
- Current behavior:
  - Runtime policy already requires current-match numeric values to come from centralized match data or a future tool.
  - When data is missing, the Agent must explain missing fields and calculation rules rather than inventing `X/Y/Z`.
- Problem:
  - Teammates still need a concrete interface contract describing which data the Agent needs, how access masking works, and what fields are mandatory for the product examples.
- Non-goals:
  - Do not implement the central service in this slice.
  - Do not add betting execution, order placement, wallet/account access, deposits, withdrawals, or platform support workflows.
  - Do not expose locked paid values to the Agent when the viewer has not unlocked the relevant block.

## Product Semantics

- User/operator workflow:
  - User asks about the current match in the Agent side panel.
  - Agent calls a read-only centralized data tool using the current `match_id` and server-side viewer context.
  - Tool returns a single consistent match snapshot that covers model probability, market price, recommendation status, strength index, model coefficients, lineup/reprice state, risk flags, and unlock masks.
  - Agent fills the Section 2 answer framework from this snapshot.
- State model:
  - The central data tool is read-only from the Agent perspective.
  - Snapshot identity must be stable through `snapshot_id` and `generated_at`.
  - No cross-session personalization is required beyond server-side unlock masking.
- Ownership and identity rules:
  - Central service owns current-match data, paid-content masking, and model snapshot provenance.
  - Chat server owns natural-language explanation, guardrails, refusal behavior, and answer formatting.
- Permissions/authentication:
  - The Agent must not accept user-supplied unlock flags.
  - The central service must derive unlock state from trusted auth/session context and either omit or mask locked values.
- Empty, error, retry, timeout, duplicate, and partial-failure behavior:
  - Missing data must be represented explicitly in `data_status`, `missing_fields`, and per-section `status`.
  - Stale data must include `stale: true`, `as_of`, and a human-readable `stale_reason`.
  - Partial data should still return public method fields so the Agent can answer calculation-method questions.
- Compatibility and migration expectations:
  - First integration can use a mock adapter with this schema.
  - Real service should remain backward compatible by adding optional fields, not renaming core fields.

## API / Interface Contract

### Recommended MVP Tool

The first useful interface should be one read-only aggregate query:

```text
tool: get_worldcup_agent_match_context
purpose: query the current match model-explanation snapshot for Agent answers
input:
  match_id: string
  locale?: "zh-Hans" | "en"
  viewer_context_ref?: string
  requested_blocks?: array<"summary" | "recommendation" | "strength_index" | "probability_model" | "market" | "lineup" | "risk">
  as_of?: string ISO-8601
output:
  WorldcupAgentMatchContext
```

`viewer_context_ref` must be an opaque trusted server reference. The tool implementation must not trust a user-provided `is_unlocked` value.

### Optional Split Queries

If the central service already separates data ownership, these can exist behind the aggregate adapter:

- `get_worldcup_recommendation_context(match_id)`: recommendation status, probability gap, odds window, no-recommendation reasons.
- `get_worldcup_strength_index_context(match_id)`: 9-dimension scores, weights, total score, Elo/SOS correction, lineup recalculation state.
- `get_worldcup_probability_context(match_id)`: expected goals, WDL probability, score grid summary, coefficients, stage calibration.
- `get_worldcup_market_context(match_id)`: Polymarket implied probabilities, executable odds, spread/depth/liquidity flags.
- `get_worldcup_unlock_context(match_id, viewer_context_ref)`: block-level unlock state and masking policy.

The Chat Server should consume the aggregate adapter even if the backend internally fans out.

### Core Response Shape

```ts
type WorldcupAgentMatchContext = {
  schema_version: "2026-06-29";
  snapshot_id: string;
  generated_at: string;
  as_of: string;
  data_status: "complete" | "partial" | "stale" | "unavailable";
  missing_fields: string[];
  match: MatchIdentity;
  access: AccessState;
  summary: MatchAgentSummary;
  recommendation: RecommendationContext;
  strength_index: StrengthIndexContext;
  probability_model: ProbabilityModelContext;
  market: MarketContext;
  lineup: LineupContext;
  risk: RiskContext;
  provenance: ProvenanceContext;
};
```

### Match Identity

```ts
type MatchIdentity = {
  match_id: string;
  competition: "WC2026";
  stage: "group" | "round_of_32" | "round_of_16" | "quarter_final" | "semi_final" | "third_place" | "final";
  kickoff_at: string;
  home_team: TeamRef;
  away_team: TeamRef;
  neutral_site?: boolean;
};

type TeamRef = {
  team_id: string;
  name: string;
  country_code?: string;
};
```

### Access State and Masking

```ts
type AccessState = {
  viewer_scope: "anonymous" | "logged_in" | "paid_unlocked" | "internal";
  blocks: {
    block_b_model_probability: BlockAccess;
    block_d_recommendation: BlockAccess;
  };
};

type BlockAccess = {
  unlocked: boolean;
  locked_reason?: "not_logged_in" | "not_paid" | "expired" | "not_available";
  mask_policy: "omit_values" | "rounded_public_summary" | "full";
};
```

For locked users, the service should return `null` for locked numeric values and include public explanatory text. Do not send exact paid values to the Agent and rely only on prompt discipline.

### Active Match Summary

```ts
type MatchAgentSummary = {
  status: "ready" | "partial" | "unavailable";
  headline_public: string;
  active_message_values: {
    match_name: string;
    home_win_probability?: number | null;
    expected_goals_home?: number | null;
    expected_goals_away?: number | null;
    recommendation_summary?: string | null;
  };
};
```

Used for match-switch active messages: match name, home win rate, expected goals, recommendation summary, and "请问您想了解什么？".

### Recommendation Context

```ts
type RecommendationContext = {
  status: "recommended" | "not_recommended" | "locked" | "insufficient_data" | "unsupported_market";
  market_side?: "home_win" | "away_win" | "draw" | "home_handicap" | "away_handicap" | "over" | "under" | null;
  recommendation_label?: string | null;
  model_probability?: number | null;
  polymarket_implied_probability?: number | null;
  probability_gap_pp?: number | null;
  trigger_threshold_pp: 4;
  decimal_odds?: number | null;
  odds_window: { min: 1.7; max: 2.4 };
  break_even_probability?: number | null;
  ev_estimate?: number | null;
  no_recommendation_reasons: RecommendationReason[];
  explanation_public: string;
};

type RecommendationReason =
  | "gap_below_4pp"
  | "odds_outside_1_70_2_40"
  | "unsupported_market"
  | "negative_or_out_of_scope_handicap"
  | "liquidity_too_thin"
  | "lineup_unconfirmed"
  | "model_confidence_low"
  | "locked"
  | "missing_probability"
  | "missing_market_price";
```

Required for Section 2.1:

- "为什么推荐主队赢盘?": needs `model_probability`, `polymarket_implied_probability`, `probability_gap_pp`, `trigger_threshold_pp`, `decimal_odds`, and `odds_window`.
- "这个赔率区间为什么没有推荐?": needs `decimal_odds`, `odds_window`, `status`, and `no_recommendation_reasons`.
- "这场为什么没有推荐投注?": needs `status` and `no_recommendation_reasons`.

### Strength Index Context

```ts
type StrengthIndexContext = {
  status: "ready" | "partial" | "locked" | "insufficient_data";
  total_score_home?: number | null;
  total_score_away?: number | null;
  score_scale: "0-100";
  dimensions_count: 9;
  dimensions: StrengthDimension[];
  elo_sos_adjustment: {
    home_adjustment?: number | null;
    away_adjustment?: number | null;
    opponent_elo_home?: number | null;
    opponent_elo_away?: number | null;
    explanation_public: string;
  };
  lineup_recalculation: {
    lineup_confirmed: boolean;
    recalculated_after_lineup: boolean;
    last_recalculated_at?: string | null;
    message_if_updated: "首发已更新，预测已重新定价";
  };
  explanation_public: string;
};

type StrengthDimension = {
  key: string;
  label: string;
  home_score?: number | null;
  away_score?: number | null;
  weight?: number | null;
  weight_source: "weight_table" | "default_small_weight" | "missing";
  is_new_dimension?: boolean;
  public_explanation: string;
};
```

Required for Section 2.2:

- "实力指数是怎么算的?": needs `dimensions_count`, `dimensions`, `score_scale`, total scores if unlocked, and `elo_sos_adjustment`.
- "为什么这个维度权重这么低?": needs `dimensions[].weight`, `weight_source`, `is_new_dimension`, and `public_explanation`.
- "这个分数会变吗?": needs `lineup_recalculation`.

### Probability Model Context

```ts
type ProbabilityModelContext = {
  status: "ready" | "partial" | "locked" | "insufficient_data";
  expected_goals: {
    home_lambda?: number | null;
    away_lambda?: number | null;
  };
  wdl_probability: {
    home_win?: number | null;
    draw?: number | null;
    away_win?: number | null;
  };
  score_grid_summary?: {
    top_scores: Array<{ score: string; probability: number }>;
    tail_5_plus_goals_probability?: number | null;
  } | null;
  coefficients: {
    total_goals_scale_k: 0.943;
    low_score_rho: -0.15;
    coefficient_source: "historical_international_match_fit";
  };
  stage_calibration: {
    stage: string;
    group_stage_confidence_contraction?: number | null;
    knockout_draw_weight_adjustment?: number | null;
    public_explanation: string;
  };
  explanation_public: string;
};
```

Required for Section 2.3 and 2.4:

- "预期进球数是多少?": needs `expected_goals.home_lambda` and `expected_goals.away_lambda`.
- "预期胜率是多少? 为什么?": needs `wdl_probability` plus `expected_goals` and model explanation.
- "预测Agent模型是什么?": can be answered from `explanation_public` and stable KB if exact match values are missing.
- "`k = 0.943` 是什么?": needs `coefficients.total_goals_scale_k`.
- "`ρ = -0.15` 是什么?": needs `coefficients.low_score_rho`.
- "小组赛和淘汰赛有什么区别?": needs `stage_calibration`.

### Market and Risk Context

```ts
type MarketContext = {
  status: "ready" | "partial" | "unavailable";
  source: "polymarket" | "internal_snapshot" | "none";
  market_id?: string | null;
  side_prices: Array<{
    side: string;
    implied_probability?: number | null;
    decimal_odds?: number | null;
    executable_ask?: number | null;
    spread?: number | null;
    depth?: number | null;
  }>;
  liquidity_flags: string[];
};

type LineupContext = {
  lineup_confirmed: boolean;
  lineup_source?: "official" | "model_input" | "unknown";
  updated_at?: string | null;
  reprice_required: boolean;
  repriced_at?: string | null;
};

type RiskContext = {
  disclaimer_required: true;
  disclaimer_text: "预测结果仅供参考，不构成投注建议，投注有风险";
  confidence_level: "high" | "medium" | "low" | "unknown";
  risk_flags: Array<
    | "big_score_tail_underestimated"
    | "early_group_stage_lower_confidence"
    | "new_dimension_subjective_weight"
    | "thin_polymarket_liquidity"
    | "lineup_unconfirmed"
    | "stale_market"
  >;
  no_bet_conditions: string[];
};
```

### Provenance and Freshness

```ts
type ProvenanceContext = {
  model_version: string;
  prompt_policy_version?: string;
  data_sources: Array<{
    name: string;
    as_of: string;
    status: "fresh" | "stale" | "missing";
  }>;
  stale: boolean;
  stale_reason?: string | null;
};
```

## Data / Schema / Projection Impact

- Tables, indexes, migrations, backfills:
  - Central-service owned; not defined in this Chat Server contract.
- Read models, projections, snapshots, caches:
  - Must expose a consistent match snapshot. If values come from multiple services, the adapter should return one `snapshot_id`.
- Historical data behavior:
  - Older snapshots may be retained by central service, but Agent should normally request current `as_of`.
- Performance-sensitive queries or write paths:
  - Target p95 under 800 ms for aggregate query, so the Agent can meet the product's first-character experience budget.

## Architecture

- Modules/files expected to change in a future implementation:
  - A read-only tool adapter for `get_worldcup_agent_match_context`.
  - Mock fixtures matching this schema.
  - Golden cases that assert filled answers with complete, partial, locked, and stale snapshots.
- Data flow:
  - Chat request -> Agent tool call -> central aggregate snapshot -> guarded natural-language answer.
- Transaction/concurrency boundaries:
  - The central adapter must be read-only and idempotent.
  - Snapshot should be internally consistent at `snapshot_id`.
- Observability/logging/metrics:
  - Log `match_id`, `snapshot_id`, `data_status`, `missing_fields`, and latency.
  - Do not log paid exact values for locked users, raw auth tokens, private account data, or provider secrets.
- Rollback strategy:
  - If real central service fails, fall back to mock/no-data mode and answer with missing-field language.

## Harness Classification

- Expected gate(s):
  - `HARNESS-SPEC-FIRST-FEATURE`
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
  - Future focused tool-adapter tests
- Performance-sensitive class:
  - Read path impacts Agent response latency, but this contract document does not implement runtime calls.
- Whether harness mapping must be extended:
  - No.
- Required performance evidence:
  - Future implementation should include mock and real-adapter latency evidence.
- Focused verification commands:
  - `scripts/check_spec_contract.sh`
  - `scripts/check_harness_workflows.sh`
- Prerelease-grade verification commands:
  - `AI_BOUNDARY_APPROVED=1 SPEC_CONTRACT_APPROVED=1 make verify-release`

## Acceptance Criteria

- Functional:
  - Teammates can implement one aggregate read-only interface that supports all Section 2 positive examples.
  - Contract defines which fields are mandatory, nullable, locked, stale, or missing.
  - Contract prevents locked Block B and Block D exact values from being sent to the Agent for locked viewers.
  - Contract includes risk flags and disclaimer text required by Section 5.
- Edge cases:
  - Locked viewer returns public explanation with exact paid values omitted.
  - Missing market price returns `missing_market_price` and allows no-recommendation explanation.
  - Missing model probability returns `missing_probability` and prevents exact win-rate answer.
  - Lineup update returns reprice state and the mandated "首发已更新，预测已重新定价" message.
  - Stale snapshot is labeled stale and must not be presented as fresh.
- Compatibility:
  - Field additions are backward-compatible.
  - Renames or semantic changes require a schema version bump.
- Operational:
  - No execution, wallet, deposit, withdrawal, or account-support data is included.
  - No raw secrets, tokens, private credentials, or locked paid values are logged.
- Evidence artifacts:
  - This specification and its implementation plan.
  - Spec-contract and Harness workflow checks.

## Review Notes

- Open questions:
  - Exact dimension keys and labels for the 9 strength-index dimensions.
  - Exact semantics for "negative_or_out_of_scope_handicap" from product copy should be confirmed with product/backend.
  - Whether odds are sourced from Polymarket, an internal odds table, or both.
  - Whether `viewer_context_ref` is available inside the Chat Server or must be injected by frontend/session middleware.
- Accepted assumptions:
  - Aggregate snapshot is preferable for Agent consistency and latency.
  - Locked paid values should be omitted/masked by the data service, not merely hidden by prompt instruction.
  - Methodological constants `k=0.943` and `ρ=-0.15` can be returned even when match-specific paid values are locked.
- Rejected alternatives:
  - Do not expose raw model internals or debug traces to the Agent as a substitute for public explanation fields.
  - Do not make the Agent query multiple live public websites for missing injury, transfer, or opinion data.
  - Do not include any real-money execution endpoint in this contract.
- Reviewer findings and resolution:
  - Pending teammate/product review.
