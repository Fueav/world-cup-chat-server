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
source /Users/chris/.codex-local/world-cup-chat-server/zai_env.sh
source /Users/chris/.codex-local/world-cup-chat-server/gemini_env.sh

export LLM_PROVIDER=zai
export ZAI_MODEL=glm-5.2
export ZAI_THINKING_TYPE=disabled
export ZAI_REASONING_EFFORT=low
export ZAI_TOOL_STREAM=true
export RAG_ENABLED=true
export RAG_VECTOR_STORE=pgvector
export EMBEDDING_PROVIDER=gemini
export EMBEDDING_MODEL=gemini-embedding-2
export EMBEDDING_DIM=256
export PROVIDER_DEFAULT_RPM=60
export PROVIDER_DEFAULT_TPM=60000
export PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS=512
export WORKER_POOL=prefork
export WORKER_CONCURRENCY=2
export REAPER_ENABLED=true
export WC2026_AGENT_API_BASE_URL=http://viki-api:8080
export WC2026_AGENT_API_TIMEOUT_S=3

envctl up \
  --name chris-world-cup-chat-server \
  --git-url git@github.com:Fueav/world-cup-chat-server.git \
  --git-ref <branch-or-sha> \
  --git-subdir dockerhost \
  --secret-env ZAI_API_KEY \
  --secret-env GEMINI_API_KEY \
  --secret-env WC2026_AGENT_API_KEY
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
