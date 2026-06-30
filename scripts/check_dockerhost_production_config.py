#!/usr/bin/env python3
"""Validate DockerHost production-readiness guardrails without extra deps."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "dockerhost" / "compose.yaml"
ENV_EXAMPLE = ROOT / "dockerhost" / "env.example"


def main() -> int:
    errors: list[str] = []
    compose = COMPOSE.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    _require("  reaper:" in compose, "dockerhost compose must define reaper service", errors)
    _require("app.tasks.reaper" in compose, "reaper service must run app.tasks.reaper", errors)
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
    ):
        _require(f"{name}=" in env_example, f"env.example must document {name}", errors)
    for name in (
        "WC2026_AGENT_API_BASE_URL",
        "WC2026_AGENT_API_KEY",
        "WC2026_AGENT_API_TIMEOUT_S",
    ):
        _require(f"{name}:" in compose, f"compose must pass {name}", errors)

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
