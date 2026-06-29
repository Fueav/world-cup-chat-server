# PROJECT CONTRACT

This repository is a World Cup match forecasting Chat Server built from the async Agent execution platform. Runtime correctness, bounded provider usage, secret hygiene, durable state, streaming behavior, and evidence-led forecasting outrank convenience.

## Build & Test Commands

```bash
make test
make verify-release
```

Direct commands:

```bash
.venv/bin/python -m pytest -q
scripts/verify_release.sh
scripts/check_ai_boundaries.sh
scripts/check_spec_contract.sh
scripts/check_harness_workflows.sh
```

## Project Conventions

- `app/api/` contains FastAPI routes and request/response integration.
- `app/runtime/` contains Agent orchestration. Keep Pydantic AI scoped to single-run orchestration.
- `app/tasks/` contains Celery/background execution only.
- `app/bus/` owns event streaming and replay behavior.
- `app/db/` owns persistence setup and database access.
- `docs/specifications/` holds implementation source-of-truth specs after a request is converted.
- `docs/implementation-plans/` holds file/module-level plans derived from specs.
- New behavior must reference stable `SPEC-*` IDs and a `Workflow Class: HARNESS-*` binding.
- World Cup forecasting behavior must preserve evidence-led reasoning: slate definition, evidence ledger, score/WDL probabilities, Polymarket executable price checks, risk conditions, and explicit no-bet outcomes.
- Logs and errors must not expose provider secrets, API keys, raw tokens, or private credentials.

## DockerHost For Integration Environments

Use DockerHost for remote disposable integration environments when this project needs PostgreSQL, Redis, pgvector, or a full API/worker stack outside the local Docker daemon.

Credential setup:

```bash
source /Users/chris/.codex-local/dockerhost/envctl_env.sh
envctl version
envctl templates
```

Important boundary:

- The DockerHost token is local-only. Do not put `ENVCTL_TOKEN` into this repository, docs, AGENTS files, prompts, logs, PRs, or test fixtures.
- The current DockerHost self-service flow deploys from pushed Git refs. Push the branch before using Git pull deployment.

For a quick plain Postgres + Redis environment:

```bash
envctl up --name <owner>-world-cup-chat-server-data --template postgres-redis
envctl status --name <owner>-world-cup-chat-server-data
```

Expose database/cache only while debugging from the Mac:

```bash
envctl expose --name <owner>-world-cup-chat-server-data --service db --ttl 30m
envctl expose --name <owner>-world-cup-chat-server-data --service cache --ttl 30m
envctl unexpose --name <owner>-world-cup-chat-server-data --service db
envctl unexpose --name <owner>-world-cup-chat-server-data --service cache
```

For pgvector/RAG work, prefer a project `dockerhost/` adapter layer using a pgvector-enabled Postgres image rather than assuming the generic `postgres-redis` template has the extension installed. The adapter should:

- use Compose service names such as `db`, `cache`, `api`, and `worker` in URLs, not `localhost`.
- use `expose:` instead of fixed host `ports:`.
- define a named Postgres volume such as `postgres-data`.
- declare the same volume in `template.yaml` `managedVolumes` with an explicit quota.
- include healthchecks for `api`, `worker`, `db`, and `cache`.
- set `CREATE EXTENSION IF NOT EXISTS vector;` in migration/init flow before pgvector tables are used.

Before deploying a project stack:

```bash
envctl check-project --dir /Users/chris/AiProject/world-cup-chat-server
envctl validate-template --dir /Users/chris/AiProject/world-cup-chat-server/dockerhost
```

Git pull deployment shape:

```bash
envctl up \
  --name <owner>-world-cup-chat-server \
  --git-url git@github.com:Fueav/world-cup-chat-server.git \
  --git-ref <branch-or-commit> \
  --git-subdir dockerhost
```

Long-lived branch-space shape:

```bash
envctl branch-space create \
  --name <owner>-world-cup-chat-server \
  --git-url git@github.com:Fueav/world-cup-chat-server.git \
  --git-ref <branch> \
  --git-subdir dockerhost

envctl branch-space deploy --name <owner>-world-cup-chat-server
envctl branch-space status --name <owner>-world-cup-chat-server
```

Pass runtime secrets with `--secret-env KEY` or `--secret-file KEY=PATH`; avoid `--secret KEY=VALUE`. Destroy disposable environments when finished:

```bash
envctl down --name <owner>-world-cup-chat-server
```

## Forbidden

- Do not let Pydantic AI absorb gateway, queue, global rate-limit, persistence, distributed scheduling, or replay responsibilities.
- Do not bypass provider/model rate-limit guardrails for real providers.
- Do not fail open in production if usage settlement or provider admission cannot be recorded.
- Do not store real provider secrets in code, tests, logs, Redis, Postgres, events, release artifacts, or docs.
- Do not add runtime/API/DB behavior from chat alone once a matching spec exists.
- Do not weaken `scripts/verify_release.sh`, AI boundary checks, or spec-contract checks to make local work easier.
- Do not migrate Ask this Agent, MOSS, wallet/copy-trading, Mint/Redeem, or Agent detail-page positioning into default World Cup forecasting behavior.
- Do not make unsupported betting claims: no guaranteed win, guaranteed profit, zero-risk,保本, or direct order execution without explicit user confirmation and a separate execution workflow.

## Testing Requirements

- New runtime behavior needs tests before implementation.
- API/streaming changes need owner/auth, idempotency, disconnect/replay, and error-path coverage.
- Provider-limit changes need quota, backoff, fail-closed, and usage-settlement tests.
- Secret-management changes need redaction and missing-secret tests.
- Release readiness is proven through `scripts/verify_release.sh`, not manual notes.
- World Cup behavior changes need golden cases covering evidence ledger, probability mapping, Polymarket price/EV semantics, no-bet conditions, and real-money refusal boundaries.

## Harness Workflows

The source of truth is `docs/harness-workflows.json`, explained by `docs/harness-workflows.md` and traced to sources in `docs/harness-source-analysis.md`.

- Start with `HARNESS-FOCUSED-CHANGE` for narrow edits.
- Use `HARNESS-SPEC-FIRST-FEATURE` for behavior, API, runtime, task, config, or persistence changes.
- Escalate to fan-out, worktree isolation, adversarial verification, loop-until-done, quarantine, model routing, or tournament workflows only when the task shape requires it.
- Every non-template spec and implementation plan must declare `Workflow Class: HARNESS-*`.
- Use `docs/harness-virtual-requirements.json` as the regression set when changing workflow classes or patterns.

## AI Boundaries

The source of truth is `.ai-boundaries.yml`.

- AI may freely edit docs, specifications, implementation plans, and tests listed as allowed.
- AI needs explicit approval for runtime/API/tasks/db/core contracts, scripts, dependencies, CI, and project guidance.
- AI must not edit forbidden paths or write private credentials into the repository.
