"""Resident async runner for realtime chat runs."""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.core.config import get_settings
from app.core.enums import RunStatus
from app.core.events import AgentEvent, EventType
from app.core.metrics import Metrics
from app.runtime.locks import RunLease


@dataclass
class RealtimeRunRequest:
    agent_run_id: str
    conversation_id: str
    user_id: str
    trace_id: str
    message: str
    metadata: dict[str, Any]
    accepted_at: float
    wc2026_context: dict[str, Any] | None = None
    route_type: str = "realtime"


@dataclass
class RealtimeRunResult:
    agent_run_id: str
    status: RunStatus
    content: str | None = None
    error: str | None = None
    degraded: bool = False


@dataclass
class RealtimeCapacitySlot:
    """A non-blocking reservation against a runner process capacity limit."""

    _release: Callable[[], None]
    released: bool = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        self._release()


class RealtimeRunner:
    """Run a single realtime chat through the Pydantic AI orchestrator."""

    def __init__(
        self,
        *,
        orchestrator_factory: Callable[[], Any] | None = None,
        run_lease: RunLease | None = None,
        runner_id: str | None = None,
        run_lease_ttl_s: int = 120,
        heartbeat_interval_s: float = 10.0,
        metrics: Metrics | None = None,
        max_concurrency: int | None = None,
        max_runtime_s: float | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self._orchestrator_factory = orchestrator_factory or self._default_orchestrator
        self._run_lease = run_lease or RunLease()
        self._runner_id = runner_id or f"{socket.gethostname()}:{id(self)}"
        self._run_lease_ttl_s = run_lease_ttl_s
        self._heartbeat_interval_s = heartbeat_interval_s
        self._metrics = metrics or Metrics()
        self._event_bus = event_bus
        self._max_runtime_s = (
            max_runtime_s if max_runtime_s is not None else get_settings().run_max_runtime_s
        )
        self._max_concurrency = (
            max_concurrency
            if max_concurrency is not None
            else get_settings().realtime_runner_max_concurrency
        )
        self._active_runs = 0
        self._reserved_runs = 0

    def try_acquire_capacity(self) -> RealtimeCapacitySlot | None:
        """Reserve one realtime slot without waiting."""
        if self._max_concurrency > 0 and self._reserved_runs >= self._max_concurrency:
            return None
        self._reserved_runs += 1
        return RealtimeCapacitySlot(self._release_capacity)

    def _release_capacity(self) -> None:
        self._reserved_runs = max(0, self._reserved_runs - 1)

    async def run_chat(
        self,
        request: RealtimeRunRequest,
        *,
        conversation_lease: Any | None = None,
        capacity_slot: RealtimeCapacitySlot | None = None,
    ) -> RealtimeRunResult:
        slot = capacity_slot or self.try_acquire_capacity()
        if slot is None:
            return RealtimeRunResult(
                agent_run_id=request.agent_run_id,
                status=RunStatus.FAILED,
                error="REALTIME_RUNNER_BUSY",
                degraded=True,
            )
        heartbeat: asyncio.Task[None] | None = None
        run_lease_started = False
        active_incremented = False
        try:
            await self._run_lease.start(
                request.agent_run_id,
                self._runner_id,
                self._run_lease_ttl_s,
            )
            run_lease_started = True
            self._active_runs += 1
            active_incremented = True
            self._metrics.set_gauge(
                "runner_active_runs",
                self._active_runs,
                {"runner_id": self._runner_id},
            )
            heartbeat = self._start_heartbeat(
                request.agent_run_id, conversation_lease
            )
            orchestrator = self._orchestrator_factory()
            content = await asyncio.wait_for(
                orchestrator.run(
                    agent_run_id=request.agent_run_id,
                    conversation_id=request.conversation_id,
                    trace_id=request.trace_id,
                    user_message=request.message,
                    accepted_at=request.accepted_at,
                    route_type=request.route_type,
                    user_id=request.user_id,
                    metadata=request.metadata,
                    wc2026_context=request.wc2026_context,
                ),
                timeout=self._max_runtime_s,
            )
            return RealtimeRunResult(
                agent_run_id=request.agent_run_id,
                status=RunStatus.SUCCEEDED,
                content=content,
            )
        except asyncio.TimeoutError:
            self._metrics.inc_counter(
                "runner_timeouts_total",
                {"runner_id": self._runner_id},
            )
            await self._finalize_timeout(request)
            return RealtimeRunResult(
                agent_run_id=request.agent_run_id,
                status=RunStatus.FAILED,
                error="RUN_TIMEOUT",
            )
        except Exception as exc:  # noqa: BLE001 runner must converge to terminal result
            return RealtimeRunResult(
                agent_run_id=request.agent_run_id,
                status=RunStatus.FAILED,
                error=str(exc),
            )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except BaseException:
                    pass
            if run_lease_started:
                try:
                    await self._run_lease.release(request.agent_run_id)
                except Exception:
                    pass
            if conversation_lease is not None:
                try:
                    await conversation_lease.release()
                except Exception:
                    pass
            if active_incremented:
                self._active_runs = max(0, self._active_runs - 1)
                self._metrics.set_gauge(
                    "runner_active_runs",
                    self._active_runs,
                    {"runner_id": self._runner_id},
                )
            await slot.release()

    def _start_heartbeat(
        self, run_id: str, conversation_lease: Any | None
    ) -> asyncio.Task[None] | None:
        if self._heartbeat_interval_s <= 0:
            return None
        return asyncio.create_task(self._heartbeat_loop(run_id, conversation_lease))

    async def _heartbeat_loop(
        self, run_id: str, conversation_lease: Any | None
    ) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_s)
            try:
                await self._run_lease.renew(run_id)
                if conversation_lease is not None:
                    await conversation_lease.renew()
            except Exception:
                continue

    @staticmethod
    def _default_orchestrator() -> Any:
        from app.runtime.deps import build_deps
        from app.runtime.orchestrator import AgentOrchestrator

        return AgentOrchestrator(build_deps())

    async def _finalize_timeout(self, request: RealtimeRunRequest) -> None:
        """Best-effort terminal state/event for runner-level timeout."""
        try:
            from app.tasks import run_store

            await run_store.mark_run_failed(request.agent_run_id, "RUN_TIMEOUT")
        except Exception:
            pass
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                f"run:{request.agent_run_id}",
                AgentEvent(
                    agent_run_id=request.agent_run_id,
                    trace_id=request.trace_id,
                    type=EventType.ERROR,
                    seq=0,
                    data={"stage": "runner", "error": "RUN_TIMEOUT"},
                ),
            )
        except Exception:
            pass


def now_seconds() -> float:
    return time.time()
