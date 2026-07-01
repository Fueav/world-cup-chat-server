#!/usr/bin/env python3
"""Validate DockerHost production-readiness guardrails without extra deps."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "dockerhost" / "compose.yaml"
ENV_EXAMPLE = ROOT / "dockerhost" / "env.example"
TEMPLATE = ROOT / "dockerhost" / "template.yaml"


def main() -> int:
    errors: list[str] = []
    compose = COMPOSE.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

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
            "${PROVIDER_DEFAULT_MAX_OUTPUT_TOKENS:-8192}"
        )
        >= 2,
        "api and worker must default provider max output tokens to 8192",
        errors,
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


if __name__ == "__main__":
    raise SystemExit(main())
