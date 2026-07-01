# World Cup Chat Server Production Readiness Runbook

## Related Runbooks

- 生产观测、Grafana MCP 日志查询、告警阈值和排障路径: [OBSERVABILITY_AND_ALERTING_RUNBOOK.md](OBSERVABILITY_AND_ALERTING_RUNBOOK.md)
- DockerHost Git pull 发布、同环境 redeploy、回滚、清理和审计: [DOCKERHOST_RELEASE_RUNBOOK.md](DOCKERHOST_RELEASE_RUNBOOK.md)

## Current Request Flow

1. Client calls `POST /chat` with Authorization or `X-API-Key`.
2. FastAPI validates body, trace id, idempotency, user ownership, user rate limit, and provider preflight.
3. The API creates or reuses a conversation, writes the user message, creates `agent_run`, and routes to realtime or batch.
4. Realtime route runs in `RealtimeRunner`; batch route enqueues Celery.
5. `AgentOrchestrator` loads history, checks provider/model quota, runs Pydantic AI, calls tools/RAG when selected, and sends model calls to Z.AI GLM-5.2.
6. RAG query/ingestion uses Gemini embedding and pgvector when enabled.
7. Events are written to Redis Stream and forwarded through SSE/WS. `Last-Event-ID` replays missed events while retained.
8. Final assistant answer and run status are persisted to Postgres.
9. Reaper scans stale queued/running work and requeues or fails it.

## DockerHost Deploy

Use local secret env files only; do not put secrets in this repository.

For existing Postgres volumes, apply this migration before routing traffic to a
build that reads `conversation.wc2026_match_id`:

```text
app/db/migrations/2026-06-30-wc2026-conversation-match-binding.sql
```

```bash
source /Users/chris/.codex-local/dockerhost/envctl_env.sh
source /Users/chris/.codex-local/general-agent-ai/zai_env.sh
source /Users/chris/.codex-local/general-agent-ai/gemini_env.sh
source /Users/chris/.codex-local/world-cup-chat-server/wc2026_agent_env.sh

export LLM_PROVIDER=zai
export RAG_ENABLED=true
export RAG_VECTOR_STORE=pgvector
export EMBEDDING_DIM=256
export WC2026_AGENT_API_BASE_URL=https://moss-dev.moss.site/api/v1
export WC2026_AGENT_API_TIMEOUT_S=10
export PROVIDER_DEFAULT_RPM=60
export PROVIDER_DEFAULT_TPM=60000
export PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS=8192
export WORKER_POOL=prefork
export WORKER_CONCURRENCY=2
export REAPER_ENABLED=true

envctl up \
  --name chris-world-cup-chat-server \
  --git-url git@github.com:Fueav/world-cup-chat-server.git \
  --git-ref <branch-or-sha> \
  --git-subdir dockerhost \
  --secret-env LLM_PROVIDER \
  --secret-env ZAI_BASE_URL \
  --secret-env ZAI_MODEL \
  --secret-env ZAI_API_KEY \
  --secret-env ZAI_THINKING_TYPE \
  --secret-env ZAI_REASONING_EFFORT \
  --secret-env ZAI_TOOL_STREAM \
  --secret-env GEMINI_API_KEY \
  --secret-env EMBEDDING_API_KEY \
  --secret-env EMBEDDING_PROVIDER \
  --secret-env EMBEDDING_MODEL \
  --secret-env RAG_ENABLED \
  --secret-env RAG_VECTOR_STORE \
  --secret-env EMBEDDING_DIM \
  --secret-env WC2026_AGENT_API_BASE_URL \
  --secret-env WC2026_AGENT_API_KEY \
  --secret-env WC2026_AGENT_API_TIMEOUT_S \
  --secret-env PROVIDER_DEFAULT_RPM \
  --secret-env PROVIDER_DEFAULT_TPM \
  --secret-env PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS \
  --secret-env WORKER_POOL \
  --secret-env WORKER_CONCURRENCY \
  --secret-env REAPER_ENABLED
```

如果目标中心化 WC2026 环境允许无 key 内网直连，可以省略
`--secret-env WC2026_AGENT_API_KEY`。当前 DockerHost dev 环境走
`https://moss-dev.moss.site/api/v1`，需要该 secret。

DockerHost 对 branch-space/redeploy 的 runtime 注入按一次性处理。不要只
`export` 本地变量，也不要只传 provider key；每次 deploy、redeploy、rollback
都要重新传入上面完整的 `--secret-env` 列表。部署后确认真实远端值：

```bash
envctl config --name chris-world-cup-chat-server --service api \
  | rg '^PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS="8192"$'
```

For correctness-first smoke fallback:

```bash
export WORKER_POOL=solo
export WORKER_CONCURRENCY=1
```

## Readiness And Metrics

```bash
BASE_URL=https://api-chris-world-cup-chat-server.dkhost.vixmk-yo.org
curl -fsS "$BASE_URL/healthz"
curl -fsS "$BASE_URL/readyz"
curl -fsS "$BASE_URL/metrics" | head
```

`/readyz` must report DB, Redis, event bus, provider secret, and provider limiter as ready before routing traffic.

## Smoke Checks

```bash
curl -fsS "$BASE_URL/chat" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${CHAT_AUTH_TOKEN:?set CHAT_AUTH_TOKEN}" \
  -d '{"message":"用一句话说明你会如何分析一场世界杯淘汰赛。","stream":true,"metadata":{"mode":"realtime"}}'
```

Use the returned `stream_url`:

```bash
curl -N -H "Authorization: Bearer ${CHAT_AUTH_TOKEN:?set CHAT_AUTH_TOKEN}" "$BASE_URL/stream/<run_id>"
curl -fsS -H "Authorization: Bearer ${CHAT_AUTH_TOKEN:?set CHAT_AUTH_TOKEN}" "$BASE_URL/runs/<run_id>"
```

RAG smoke:

1. Use an identity included in `RAG_ADMIN_USER_IDS`; ordinary `smoke-user` must not call `/rag/*`.
2. Create a knowledge base.
3. Import one short document.
4. Wait until ingestion job succeeds.
5. Query the knowledge base through `/rag/query` only as the internal RAG admin and confirm `degraded=false`.
6. To let ordinary Chat consume that KB, deploy with `RAG_DEFAULT_KNOWLEDGE_BASE_ID=<kb_id>` and `RAG_INTERNAL_OWNER_USER_ID=<rag_admin_user_id>`.

## Load Smoke

Bounded TTFT smoke:

```bash
.venv/bin/python scripts/benchmark_realtime_ttft.py \
  --base-url "$BASE_URL" \
  --requests 20 \
  --concurrency 5 \
  --stream-timeout 45 \
  --request-timeout 45
```

Record p95 TTFT, error rate, and `/metrics` output in release notes.

## Rollback

1. Route new traffic to batch:
   ```bash
   export CHAT_RUNTIME_MODE=celery
   ```
2. If worker concurrency is the issue:
   ```bash
   export WORKER_POOL=solo
   export WORKER_CONCURRENCY=1
   ```
3. If provider is the issue:
   ```bash
   export LLM_PROVIDER=mock
   export RAG_ENABLED=false
   ```
4. Redeploy a previous known-good Git sha through DockerHost.

## Backups And Data Safety

- Postgres is the authoritative store for conversations, messages, runs, RAG documents, chunks, and embeddings.
- For long-lived environments, configure DockerHost volume backup or promote the database to managed Postgres before production traffic.
- Redis Stream is hot replay state, not the final system of record.
- Do not destroy a DockerHost environment with production data unless Postgres backup/restore has been verified.

## Residual Risks Before Full Production

- Formal auth/tenant/API-key integration is intentionally pending.
- Managed dashboards and alert rules still need to be wired to the `/metrics` surface.
- Final capacity targets require a real quota-informed load test.
- Realtime runs are not durable mid-agent graph resumes; crashes are recovered by failing/retrying at run level.
- Secret rotation is env-injection based; operational rotation procedure must be owned by the deployment platform.
