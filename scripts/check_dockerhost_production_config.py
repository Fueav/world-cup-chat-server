#!/usr/bin/env python3
"""Validate DockerHost production-readiness guardrails without extra deps."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "dockerhost" / "compose.yaml"
ENV_EXAMPLE = ROOT / "dockerhost" / "env.example"
TEMPLATE = ROOT / "dockerhost" / "template.yaml"
APP_CONFIG = ROOT / "app" / "core" / "config.py"
AGENTS = ROOT / "AGENTS.md"
PRODUCTION_RUNBOOK = ROOT / "docs" / "PRODUCTION_READINESS_RUNBOOK.md"
DOCKERHOST_RELEASE_RUNBOOK = ROOT / "docs" / "DOCKERHOST_RELEASE_RUNBOOK.md"
EXPECTED_PROVIDER_MAX_OUTPUT_TOKENS = "8192"
EXPECTED_STABLE_RUNTIME_ENV = {
    "LLM_PROVIDER": "zai",
    "RAG_ENABLED": "true",
    "RAG_VECTOR_STORE": "pgvector",
    "EMBEDDING_DIM": "256",
    "WC2026_AGENT_API_BASE_URL": "https://moss-dev.moss.site/api/v1",
    "WC2026_AGENT_API_TIMEOUT_S": "10",
    "PROVIDER_DEFAULT_RPM": "60",
    "PROVIDER_DEFAULT_TPM": "60000",
    "PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS": EXPECTED_PROVIDER_MAX_OUTPUT_TOKENS,
    "WORKER_POOL": "prefork",
    "WORKER_CONCURRENCY": "2",
    "REAPER_ENABLED": "true",
}
EXPECTED_STABLE_SECRET_ENVS = (
    "LLM_PROVIDER",
    "ZAI_BASE_URL",
    "ZAI_MODEL",
    "ZAI_API_KEY",
    "ZAI_THINKING_TYPE",
    "ZAI_REASONING_EFFORT",
    "ZAI_TOOL_STREAM",
    "GEMINI_API_KEY",
    "EMBEDDING_API_KEY",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_MODEL",
    "RAG_ENABLED",
    "RAG_VECTOR_STORE",
    "EMBEDDING_DIM",
    "WC2026_AGENT_API_BASE_URL",
    "WC2026_AGENT_API_KEY",
    "WC2026_AGENT_API_TIMEOUT_S",
    "PROVIDER_DEFAULT_RPM",
    "PROVIDER_DEFAULT_TPM",
    "PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS",
    "WORKER_POOL",
    "WORKER_CONCURRENCY",
    "REAPER_ENABLED",
)
LOCAL_SOURCE_FILES = (
    "source /Users/chris/.codex-local/dockerhost/envctl_env.sh",
    "source /Users/chris/.codex-local/general-agent-ai/zai_env.sh",
    "source /Users/chris/.codex-local/general-agent-ai/gemini_env.sh",
    "source /Users/chris/.codex-local/world-cup-chat-server/wc2026_agent_env.sh",
)


def main() -> int:
    errors: list[str] = []
    compose = COMPOSE.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")
    app_config = APP_CONFIG.read_text(encoding="utf-8")
    agents = AGENTS.read_text(encoding="utf-8")
    production_runbook = PRODUCTION_RUNBOOK.read_text(encoding="utf-8")
    dockerhost_runbook = DOCKERHOST_RELEASE_RUNBOOK.read_text(encoding="utf-8")

    _require("  reaper:" in compose, "dockerhost compose must define reaper service", errors)
    _require("app.tasks.reaper" in compose, "reaper service must run app.tasks.reaper", errors)
    _require(
        "proxy:\n      requestTimeout: 0" in template,
        "api web service must disable DockerHost request timeout for SSE streams",
        errors,
    )
    _require(
        "${WORKER_POOL:-prefork}" in compose,
        "worker pool must be env-configurable with prefork default",
        errors,
    )
    _require(
        "${WORKER_CONCURRENCY:-2}" in compose,
        "worker concurrency must be env-configurable with >1 default",
        errors,
    )
    _require(
        "python -m app.tasks.reaper --once --dry-run" in compose,
        "reaper healthcheck must run a bounded dry-run scan",
        errors,
    )
    for name in (
        "RUN_MAX_RUNTIME_S",
        "STREAM_MAXLEN",
        "METRICS_ENABLED",
        "REAPER_ENABLED",
        "REAPER_INTERVAL_S",
        "REAPER_STALE_AFTER_S",
        "REAPER_MAX_ATTEMPTS",
        "WORKER_POOL",
        "WORKER_CONCURRENCY",
        "WC2026_AGENT_API_BASE_URL",
        "WC2026_AGENT_API_TIMEOUT_S",
        "PROVIDER_KEY_POOL_FILE",
        "PROVIDER_KEY_POOL_SCOPE",
        "PROVIDER_KEY_POOL_STRATEGY",
        "ZAI_API_KEYS_FILE",
    ):
        _require(f"{name}=" in env_example, f"env.example must document {name}", errors)
    for name in (
        "WC2026_AGENT_API_BASE_URL",
        "WC2026_AGENT_API_KEY",
        "WC2026_AGENT_API_TIMEOUT_S",
    ):
        _require(f"{name}:" in compose, f"compose must pass {name}", errors)
    _require(
        compose.count("ZAI_REASONING_EFFORT: ${ZAI_REASONING_EFFORT:-medium}") >= 2,
        "api and worker must default ZAI_REASONING_EFFORT to medium",
        errors,
    )
    _require(
        compose.count(
            "PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS: "
            f"${{PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS:-{EXPECTED_PROVIDER_MAX_OUTPUT_TOKENS}}}"
        )
        >= 3,
        "api, worker, and reaper must default provider max output tokens to 8192",
        errors,
    )
    _require(
        f"PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS={EXPECTED_PROVIDER_MAX_OUTPUT_TOKENS}"
        in env_example,
        "dockerhost env.example must default provider max output tokens to 8192",
        errors,
    )
    _require(
        (
            "provider_default_max_output_tokens: int = "
            f"{EXPECTED_PROVIDER_MAX_OUTPUT_TOKENS}"
        )
        in app_config,
        "Settings must default provider max output tokens to 8192",
        errors,
    )
    _require_stable_deploy_docs("AGENTS.md", agents, errors)
    _require_stable_deploy_docs(
        "docs/PRODUCTION_READINESS_RUNBOOK.md",
        production_runbook,
        errors,
    )
    _require_stable_deploy_docs(
        "docs/DOCKERHOST_RELEASE_RUNBOOK.md",
        dockerhost_runbook,
        errors,
        require_source_files=False,
    )
    for name in (
        "PROVIDER_KEY_POOL_FILE",
        "PROVIDER_KEY_POOL_SCOPE",
        "PROVIDER_KEY_POOL_STRATEGY",
        "ZAI_API_KEYS_FILE",
    ):
        _require(
            compose.count(f"{name}:") >= 2,
            f"api and worker must pass {name}",
            errors,
        )

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1
    print("PASS dockerhost production config")
    return 0


def _require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def _require_stable_deploy_docs(
    label: str,
    text: str,
    errors: list[str],
    *,
    require_source_files: bool = True,
) -> None:
    if require_source_files:
        for source_line in LOCAL_SOURCE_FILES:
            _require(
                source_line in text,
                f"{label} must source local DockerHost/provider/WC2026 env files",
                errors,
            )
    for name, value in EXPECTED_STABLE_RUNTIME_ENV.items():
        _require(
            _has_export_assignment(text, name, value),
            f"{label} must document export {name}={value}",
            errors,
        )
    for name in EXPECTED_STABLE_SECRET_ENVS:
        _require(
            f"--secret-env {name}" in text,
            f"{label} must pass --secret-env {name} for DockerHost deploys",
            errors,
        )
    _require(
        "PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS=512" not in text
        and "${PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS:-512}" not in text,
        f"{label} must not document a 512 provider output-token budget",
        errors,
    )


def _has_export_assignment(text: str, name: str, value: str) -> bool:
    pattern = rf"^export\s+{re.escape(name)}={re.escape(value)}$"
    return re.search(pattern, text, flags=re.MULTILINE) is not None


if __name__ == "__main__":
    raise SystemExit(main())
