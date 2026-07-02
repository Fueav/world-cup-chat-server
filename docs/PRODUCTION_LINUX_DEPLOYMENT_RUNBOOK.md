# World Cup Chat Server Docker Compose 生产部署手册

生产环境使用 Docker Compose 部署 `api`、`worker`、`reaper` 三个服务。Postgres 和 Redis 使用云服务厂商托管实例，Compose 里不启动数据库和缓存。所有 `<...>` 替换成生产值；真实密钥不要写入仓库、日志、工单、截图或 shell history。

## 1. 部署结构

| 组件 | 部署方式 | 说明 |
| --- | --- | --- |
| API | Compose service `api` | HTTP/SSE/WS、鉴权、限流、realtime runner |
| Worker | Compose service `worker` | batch run、RAG ingestion、异步任务 |
| Reaper | Compose service `reaper` | 回收 stale run |
| Postgres 16 + pgvector | 云数据库 | conversation、message、run、RAG 文档和向量 |
| Redis 7 | 云 Redis | broker、result、stream、lock、限流桶 |
| Nginx/TLS | 宿主机或运维统一入口 | `443 -> 127.0.0.1:8080` |

不要直接把当前 `dockerhost/compose.yaml` 当生产文件使用：它包含本地 `db` 和 `cache` 服务。生产 Compose 文件只保留应用服务。

建议宿主机路径：

```text
/opt/world-cup-chat-server/current
/etc/world-cup-chat-server/.env.production
/etc/world-cup-chat-server/secrets/
```

## 2. 云 PG / Redis 准备

Postgres 必须启用 pgvector，并执行 schema 初始化或迁移。迁移用的 admin/backup URL 只在运维 shell 里设置，不放进应用容器环境。新库：

```bash
psql "$DATABASE_URL_FOR_ADMIN" -c 'CREATE EXTENSION IF NOT EXISTS vector;'
psql "$DATABASE_URL_FOR_ADMIN" -f app/db/init.sql
```

已有库升级前先备份，再执行当前 migration：

```bash
pg_dump "$DATABASE_URL_FOR_BACKUP" > "/backup/worldcup_chat_$(date +%Y%m%d%H%M%S).sql"
psql "$DATABASE_URL_FOR_ADMIN" \
  -f app/db/migrations/2026-06-30-wc2026-conversation-match-binding.sql
```

Redis 使用同一个云实例的 3 个 DB，或按云厂商规范拆实例。运行时只通过 `.env.production` 里的变量渲染进 Compose：

```text
REDIS_URL=<REDIS_URL>
CELERY_BROKER_URL=<CELERY_BROKER_URL>
CELERY_RESULT_BACKEND=<CELERY_RESULT_BACKEND>
```

云 Redis 不要开放公网；开启密码/ACL；确认内存策略不会淘汰队列和事件流。

## 3. 发布代码和配置

```bash
export RELEASE_SHA=<git-sha>
export APP_ROOT=/opt/world-cup-chat-server
export RELEASE_DIR="$APP_ROOT/releases/$RELEASE_SHA"

git clone git@github.com:Fueav/world-cup-chat-server.git "$RELEASE_DIR"
cd "$RELEASE_DIR"
git checkout "$RELEASE_SHA"

PYTHON=python3 AI_BOUNDARY_APPROVED=1 scripts/verify_release.sh
ln -sfn "$RELEASE_DIR" "$APP_ROOT/current"
```

如果生产机不跑完整验证，CI/发布机必须对同一 SHA 跑过 `scripts/verify_release.sh`。

创建配置和密钥文件：

```bash
sudo mkdir -p /etc/world-cup-chat-server/secrets
sudo install -m 0640 /dev/null /etc/world-cup-chat-server/.env.production
sudo install -m 0640 /dev/null /etc/world-cup-chat-server/secrets/zai_api_key
sudo install -m 0640 /dev/null /etc/world-cup-chat-server/secrets/embedding_api_key
```

`/etc/world-cup-chat-server/.env.production`：

```bash
APP_PORT=8080
LOG_LEVEL=INFO

DB_URL=<DB_URL>
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=0
REDIS_URL=<REDIS_URL>
CELERY_BROKER_URL=<CELERY_BROKER_URL>
CELERY_RESULT_BACKEND=<CELERY_RESULT_BACKEND>

LLM_PROVIDER=zai
ZAI_BASE_URL=https://api.z.ai/api/paas/v4/
ZAI_MODEL=glm-5.2
ZAI_API_KEY_FILE=/run/wcchat-secrets/zai_api_key
ZAI_THINKING_TYPE=enabled
ZAI_REASONING_EFFORT=medium
ZAI_TOOL_STREAM=true

WC2026_AGENT_API_BASE_URL=https://moss-dev.moss.site/api/v1
WC2026_AGENT_API_KEY=<secret-if-required>
WC2026_AGENT_API_TIMEOUT_S=10

RAG_ENABLED=true
RAG_VECTOR_STORE=pgvector
EMBEDDING_PROVIDER=gemini
EMBEDDING_MODEL=<embedding-model>
EMBEDDING_API_KEY_FILE=/run/wcchat-secrets/embedding_api_key
EMBEDDING_DIM=256

PROVIDER_RATE_LIMIT_ENABLED=true
PROVIDER_RATE_LIMIT_FAIL_OPEN=false
PROVIDER_DEFAULT_RPM=60
PROVIDER_DEFAULT_TPM=60000
PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS=8192
CHAT_RUNTIME_MODE=auto
WORKER_POOL=prefork
WORKER_CONCURRENCY=2
REAPER_ENABLED=true
REAPER_INTERVAL_S=30
REAPER_STALE_AFTER_S=300
REAPER_MAX_ATTEMPTS=3
```

写入 provider key：

```bash
sudo editor /etc/world-cup-chat-server/secrets/zai_api_key
sudo editor /etc/world-cup-chat-server/secrets/embedding_api_key
```

多把 Z.AI key 时，用一行一个 key 的文件，并在 env 中替换单 key：

```bash
ZAI_API_KEYS_FILE=/run/wcchat-secrets/zai_keys.txt
PROVIDER_KEY_POOL_SCOPE=key
```

复杂 key pool 用：

```bash
PROVIDER_KEY_POOL_FILE=/run/wcchat-secrets/provider_key_pool.json
```

当前代码没有 `WC2026_AGENT_API_KEY_FILE`，所以 `WC2026_AGENT_API_KEY` 只能放在受限权限的 `.env.production`。

## 4. 生产 Compose 文件

在 `/opt/world-cup-chat-server/current/compose.prod.yaml` 创建：

```yaml
name: world-cup-chat-server

x-app: &app
  build:
    context: .
    dockerfile: dockerhost/Dockerfile
  env_file:
    - /etc/world-cup-chat-server/.env.production
  # PG / Redis 只从 .env.production 渲染，不在 Compose 里写死。
  environment:
    DB_URL: ${DB_URL}
    DB_POOL_SIZE: ${DB_POOL_SIZE}
    DB_MAX_OVERFLOW: ${DB_MAX_OVERFLOW}
    REDIS_URL: ${REDIS_URL}
    CELERY_BROKER_URL: ${CELERY_BROKER_URL}
    CELERY_RESULT_BACKEND: ${CELERY_RESULT_BACKEND}
  volumes:
    - /etc/world-cup-chat-server/secrets:/run/wcchat-secrets:ro
  restart: unless-stopped

services:
  api:
    <<: *app
    command: ["python", "-m", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
    ports:
      - "127.0.0.1:8080:8080"
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=2).read()\""]
      interval: 10s
      timeout: 3s
      retries: 12
      start_period: 20s

  worker:
    <<: *app
    command: >
      sh -lc 'python -m celery -A app.tasks.celery_app:celery_app worker
      -l info --pool="$WORKER_POOL" --concurrency="$WORKER_CONCURRENCY"
      -Q q.run,q.intent,q.rag,q.tool,q.llm,q.compose'
    healthcheck:
      test: ["CMD-SHELL", "python -m celery -A app.tasks.celery_app:celery_app inspect ping --timeout=5 >/tmp/celery-ping.log 2>&1 || exit 1"]
      interval: 20s
      timeout: 8s
      retries: 6
      start_period: 30s

  reaper:
    <<: *app
    command: >
      sh -lc 'python -m app.tasks.reaper
      --interval-s "$REAPER_INTERVAL_S"
      --stale-after-s "$REAPER_STALE_AFTER_S"
      --max-attempts "$REAPER_MAX_ATTEMPTS"'
    healthcheck:
      test: ["CMD-SHELL", "python -m app.tasks.reaper --once --dry-run --stale-after-s ${REAPER_STALE_AFTER_S:-300} --max-attempts ${REAPER_MAX_ATTEMPTS:-3} >/tmp/reaper-health.log 2>&1 || exit 1"]
      interval: 30s
      timeout: 12s
      retries: 4
      start_period: 30s
```

启动：

```bash
cd /opt/world-cup-chat-server/current
docker compose --env-file /etc/world-cup-chat-server/.env.production -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs --tail=100 api worker reaper
```

## 5. Nginx

如果宿主机 Nginx 负责 TLS，反代到 Compose 绑定的 `127.0.0.1:8080`：

```nginx
server {
    listen 80;
    server_name <domain>;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name <domain>;
    ssl_certificate /etc/letsencrypt/live/<domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<domain>/privkey.pem;
    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 360s;
        proxy_send_timeout 360s;
    }
}
```

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 6. 发布验证

```bash
export BASE_URL=https://<domain>

curl -fsS "$BASE_URL/healthz"
curl -fsS "$BASE_URL/readyz"
docker compose -f /opt/world-cup-chat-server/current/compose.prod.yaml ps
docker compose -f /opt/world-cup-chat-server/current/compose.prod.yaml logs --tail=100 api worker reaper
docker compose -f /opt/world-cup-chat-server/current/compose.prod.yaml exec worker \
  python -m celery -A app.tasks.celery_app:celery_app inspect ping --timeout=5
docker compose -f /opt/world-cup-chat-server/current/compose.prod.yaml exec reaper \
  python -m app.tasks.reaper --once --dry-run
```

`stream=false` 必须返回 `422 STREAM_FALSE_NOT_SUPPORTED`：

```bash
export USER_UUID=prod-smoke-$(date +%s)
curl -sS -o /tmp/wc-stream-false.json -w "%{http_code}\n" \
  "$BASE_URL/api/v1/wc2026/chat?user_uuid=$USER_UUID" \
  -H 'Content-Type: application/json' \
  -d '{"message":"smoke: stream=false must be rejected","stream":false}'
cat /tmp/wc-stream-false.json
```

Accepted chat + SSE 必须最终 `RUN_COMPLETED`：

```bash
cat >/tmp/wc2026_smoke_body.json <<'JSON'
{"message":"用一句话说明世界杯预测需要哪些证据。","stream":true,"metadata":{"release_smoke":true},"wc2026_context":{"current_match_id":"83","current_match":{"id":"83","fd_match_id":"83","description":"墨西哥 vs 厄瓜多尔","stage":"group","stage_label":"小组赛","home":{"name":"墨西哥","short_name":"MEX"},"away":{"name":"厄瓜多尔","short_name":"ECU"},"is_unlocked":true},"entitlements":{"has_all":false,"unlocked_matches":["83"],"locked_matches":[]}}}
JSON

curl -fsS "$BASE_URL/api/v1/wc2026/chat?user_uuid=$USER_UUID" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/wc2026_smoke_body.json \
  | tee /tmp/wc-chat-accepted.json

export RUN_ID=$(python3 -c 'import json; print(json.load(open("/tmp/wc-chat-accepted.json"))["agent_run_id"])')
export STREAM_URL=$(python3 -c 'import json; print(json.load(open("/tmp/wc-chat-accepted.json"))["stream_url"])')
timeout 120 curl -fsS -N "$BASE_URL$STREAM_URL" | tee /tmp/wc-sse.log
grep -q "RUN_COMPLETED" /tmp/wc-sse.log
curl -fsS "$BASE_URL/api/v1/wc2026/runs/$RUN_ID?user_uuid=$USER_UUID"
```

通过标准：`/readyz` ready；accepted response 有 `conversation_id`、`agent_run_id`、`stream_url`；run status 为 `SUCCEEDED`；输出和错误不含 provider key、DB 密码或 Redis 密码。

## 7. 回滚和排障

代码回滚：

```bash
export PREVIOUS_RELEASE=/opt/world-cup-chat-server/releases/<previous-good-sha>
test -d "$PREVIOUS_RELEASE"
ln -sfn "$PREVIOUS_RELEASE" /opt/world-cup-chat-server/current
cd /opt/world-cup-chat-server/current
docker compose --env-file /etc/world-cup-chat-server/.env.production -f compose.prod.yaml up -d --build
curl -fsS "$BASE_URL/readyz"
```

不要默认做 DB schema 回滚。只有 schema 与旧代码不兼容，并且有备份和恢复演练时，才按数据库团队流程 restore。

降级开关：

```bash
LLM_PROVIDER=mock
RAG_ENABLED=false

WORKER_POOL=solo
WORKER_CONCURRENCY=1
```

修改 `.env.production` 后执行：

```bash
cd /opt/world-cup-chat-server/current
docker compose --env-file /etc/world-cup-chat-server/.env.production -f compose.prod.yaml up -d
```

| 现象 | 先查 |
| --- | --- |
| `/readyz` 里 `db=error` | 云 PG 连接串、白名单、pgvector、schema、连接数、账号权限 |
| `/readyz` 里 `redis=error` | 云 Redis URL、密码、ACL、白名单、DB index |
| `/readyz` 里 `provider_secret=missing` | `LLM_PROVIDER` 和 `/run/wcchat-secrets/*` 挂载权限 |
| accepted 后 SSE 无终态 | `docker compose logs api worker reaper`、Redis stream、run status、provider timeout |
| 大量 provider 429 | `PROVIDER_DEFAULT_RPM/TPM`、key pool scope、worker concurrency、实际配额 |
| RAG 失败 | `RAG_VECTOR_STORE=pgvector`、embedding key、`EMBEDDING_DIM`、chunk 表数据 |
| Worker 不消费 | Celery broker URL、云 Redis DB 1、worker 健康检查、队列名 |
| Reaper 异常 | reaper logs、云 PG/Redis 权限、stale 参数 |

每次部署至少记录：时间、操作者、域名、commit SHA、DB migration、配置变更、`/readyz` 结果、smoke 的 `conversation_id` 和 `agent_run_id`、是否回滚。
