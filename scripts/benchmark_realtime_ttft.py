"""Local realtime chat TTFT benchmark.

This script drives the real FastAPI service over HTTP and SSE. It measures the
client-observable time from starting the WC2026 chat endpoint until the first
TOKEN SSE event for each run, using local Postgres and Redis behind the API
service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

WC2026_API_PREFIX = "/api/v1/wc2026"


@dataclass
class Sample:
    ok: bool
    status_code: int | None
    e2e_ttft_ms: float | None
    stream_ttft_ms: float | None
    post_ms: float | None
    total_ms: float | None
    agent_run_id: str | None = None
    error: str | None = None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percentile / 100) * (len(ordered) - 1))))
    return ordered[index]


async def _wait_ready(base_url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{base_url}/readyz")
                if response.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError(f"service not ready: {base_url}")


async def _read_sse_until_done(
    client: httpx.AsyncClient,
    base_url: str,
    run_id: str,
    user_id: str,
    started_at: float,
    post_finished_at: float,
) -> tuple[float | None, float]:
    first_token_at: float | None = None
    event_name: str | None = None
    async with client.stream(
        "GET",
        _wc2026_url(base_url, f"/stream/{run_id}", user_id),
    ) as response:
        response.raise_for_status()
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
                if event_name == "TOKEN" and first_token_at is None:
                    first_token_at = time.perf_counter()
                continue
            if line.startswith("data: ") and event_name == "RUN_COMPLETED":
                return (
                    None
                    if first_token_at is None
                    else (first_token_at - post_finished_at) * 1000,
                    (time.perf_counter() - started_at) * 1000,
                )
    return (
        None if first_token_at is None else (first_token_at - post_finished_at) * 1000,
        (time.perf_counter() - started_at) * 1000,
    )


async def _one_run(
    client: httpx.AsyncClient,
    base_url: str,
    index: int,
    run_prefix: str,
    stream_timeout_s: float,
) -> Sample:
    user_id = f"bench-user-{run_prefix}-{index}"
    started_at = time.perf_counter()
    try:
        response = await client.post(
            _wc2026_url(base_url, "/chat", user_id),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": f"bench-{run_prefix}-{index}",
            },
            json={
                "message": f"benchmark message {run_prefix} #{index}",
                "stream": True,
                "metadata": {"mode": "realtime"},
            },
        )
        if response.status_code != 202:
            return Sample(
                ok=False,
                status_code=response.status_code,
                e2e_ttft_ms=None,
                stream_ttft_ms=None,
                post_ms=(time.perf_counter() - started_at) * 1000,
                total_ms=(time.perf_counter() - started_at) * 1000,
                error=response.text[:300],
            )
        post_finished_at = time.perf_counter()
        payload = response.json()
        run_id = payload["agent_run_id"]
        stream_ttft_ms, total_ms = await asyncio.wait_for(
            _read_sse_until_done(
                client,
                base_url,
                run_id,
                user_id,
                started_at,
                post_finished_at,
            ),
            timeout=stream_timeout_s,
        )
        e2e_ttft_ms = (
            None
            if stream_ttft_ms is None
            else (post_finished_at - started_at) * 1000 + stream_ttft_ms
        )
        return Sample(
            ok=stream_ttft_ms is not None,
            status_code=response.status_code,
            e2e_ttft_ms=e2e_ttft_ms,
            stream_ttft_ms=stream_ttft_ms,
            post_ms=(post_finished_at - started_at) * 1000,
            total_ms=total_ms,
            agent_run_id=run_id,
        )
    except Exception as exc:
        return Sample(
            ok=False,
            status_code=None,
            e2e_ttft_ms=None,
            stream_ttft_ms=None,
            post_ms=None,
            total_ms=(time.perf_counter() - started_at) * 1000,
            error=repr(exc),
        )


def _wc2026_url(base_url: str, path: str, user_uuid: str) -> str:
    encoded_user = quote(user_uuid, safe="")
    return f"{base_url}{WC2026_API_PREFIX}{path}?user_uuid={encoded_user}"


async def _run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    await _wait_ready(args.base_url, timeout_s=args.ready_timeout)
    limits = httpx.Limits(
        max_connections=args.max_connections,
        max_keepalive_connections=args.max_connections,
    )
    timeout = httpx.Timeout(args.request_timeout)
    run_prefix = f"{int(time.time())}-{args.requests}-{args.concurrency}"
    samples: list[Sample] = []
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        async def worker(index: int) -> None:
            async with semaphore:
                sample = await _one_run(
                    client,
                    args.base_url,
                    index,
                    run_prefix,
                    args.stream_timeout,
                )
                samples.append(sample)

        await asyncio.gather(*(worker(i) for i in range(args.requests)))

    elapsed_s = time.perf_counter() - started
    ok_samples = [
        sample for sample in samples if sample.ok and sample.e2e_ttft_ms is not None
    ]
    e2e_ttfts = [
        sample.e2e_ttft_ms for sample in ok_samples if sample.e2e_ttft_ms is not None
    ]
    stream_ttfts = [
        sample.stream_ttft_ms
        for sample in ok_samples
        if sample.stream_ttft_ms is not None
    ]
    posts = [sample.post_ms for sample in ok_samples if sample.post_ms is not None]
    totals = [sample.total_ms for sample in ok_samples if sample.total_ms is not None]
    errors = [sample for sample in samples if not sample.ok]
    summary = {
        "base_url": args.base_url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "ok": len(ok_samples),
        "errors": len(errors),
        "error_rate": round(len(errors) / max(1, len(samples)), 4),
        "elapsed_s": round(elapsed_s, 3),
        "throughput_rps": round(len(samples) / elapsed_s, 2) if elapsed_s else None,
        "e2e_ttft_ms": {
            "description": "POST start -> first TOKEN SSE",
            "min": None if not e2e_ttfts else round(min(e2e_ttfts), 2),
            "median": None if not e2e_ttfts else round(statistics.median(e2e_ttfts), 2),
            "p90": None if not e2e_ttfts else round(_percentile(e2e_ttfts, 90) or 0, 2),
            "p95": None if not e2e_ttfts else round(_percentile(e2e_ttfts, 95) or 0, 2),
            "p99": None if not e2e_ttfts else round(_percentile(e2e_ttfts, 99) or 0, 2),
            "max": None if not e2e_ttfts else round(max(e2e_ttfts), 2),
        },
        "post_accept_ms": {
            "description": "POST start -> 202 response received",
            "median": None if not posts else round(statistics.median(posts), 2),
            "p95": None if not posts else round(_percentile(posts, 95) or 0, 2),
            "max": None if not posts else round(max(posts), 2),
        },
        "stream_ttft_ms": {
            "description": "202 response received -> first TOKEN SSE",
            "median": None if not stream_ttfts else round(statistics.median(stream_ttfts), 2),
            "p95": None if not stream_ttfts else round(_percentile(stream_ttfts, 95) or 0, 2),
            "max": None if not stream_ttfts else round(max(stream_ttfts), 2),
        },
        "total_ms": {
            "median": None if not totals else round(statistics.median(totals), 2),
            "p95": None if not totals else round(_percentile(totals, 95) or 0, 2),
            "max": None if not totals else round(max(totals), 2),
        },
        "sample_errors": [
            {
                "status_code": sample.status_code,
                "total_ms": None if sample.total_ms is None else round(sample.total_ms, 2),
                "error": sample.error,
            }
            for sample in errors[:5]
        ],
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark realtime chat TTFT")
    parser.add_argument("--base-url", default="http://127.0.0.1:8012")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--max-connections", type=int, default=2000)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--stream-timeout", type=float, default=30.0)
    parser.add_argument("--ready-timeout", type=float, default=10.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run_benchmark(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
