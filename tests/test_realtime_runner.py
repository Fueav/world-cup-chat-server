from __future__ import annotations

import asyncio

from tests.harness_fakes import (
    FakeClock,
    FakeDbSessionTracker,
    FakeLockLease,
    FakeRunLease,
    FakeStreamingModel,
)


async def test_realtime_runner_releases_db_before_streaming():
    tracker = FakeDbSessionTracker()

    async with tracker.checkout():
        pass

    tracker.start_streaming()
    async for _ in FakeStreamingModel(["hello", " world"]).stream():
        assert tracker.active_connections == 0
    tracker.stop_streaming()

    assert tracker.active_during_streaming == 0


async def test_first_token_flushes_before_aggregation_window():
    clock = FakeClock()
    model = FakeStreamingModel(["A", "B"], delay_s=0.05, clock=clock)
    flush_times: list[float] = []

    async for token in model.stream():
        flush_times.append(clock.now())
        if token == "A":
            break

    assert flush_times == [0.0]
    assert clock.now() < 0.05


async def test_realtime_runner_returns_success_and_releases_leases():
    from app.core.enums import RunStatus
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner

    class _Orchestrator:
        calls = []

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            return "answer"

    run_lease = FakeRunLease()
    conversation_lease = FakeLockLease("conv-1")
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=run_lease,
        runner_id="runner-test",
        heartbeat_interval_s=0,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert result.content == "answer"
    assert await run_lease.is_alive("run-1") is False
    assert conversation_lease.released is True


async def test_realtime_runner_failure_releases_leases_and_returns_failed():
    from app.core.enums import RunStatus
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner

    class _Orchestrator:
        async def run(self, **kwargs):
            raise RuntimeError("boom")

    conversation_lease = FakeLockLease("conv-1")
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=FakeRunLease(),
        runner_id="runner-test",
        heartbeat_interval_s=0,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.FAILED
    assert "boom" in (result.error or "")
    assert conversation_lease.released is True


async def test_realtime_runner_pre_run_failure_emits_terminal_event(monkeypatch):
    from app.core.enums import RunStatus
    from app.core.events import EventType
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner
    from app.tasks import run_store

    failed_runs: list[tuple[str, str]] = []

    async def _mark_failed(run_id: str, error: str) -> None:
        failed_runs.append((run_id, error))

    class _EventBus:
        def __init__(self) -> None:
            self.events = []

        async def publish(self, channel, event):
            self.events.append((channel, event))
            return event

    def _factory():
        raise RuntimeError("build deps failed")

    monkeypatch.setattr(run_store, "mark_run_failed", _mark_failed)
    event_bus = _EventBus()
    run_lease = FakeRunLease()
    conversation_lease = FakeLockLease("conv-1")
    runner = RealtimeRunner(
        orchestrator_factory=_factory,
        run_lease=run_lease,
        runner_id="runner-test",
        heartbeat_interval_s=0,
        event_bus=event_bus,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-pre-fail",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.FAILED
    assert failed_runs == [("run-pre-fail", "REALTIME_RUNNER_FAILED")]
    assert [event.type for _, event in event_bus.events] == [
        EventType.ERROR,
        EventType.RUN_COMPLETED,
    ]
    assert event_bus.events[-1][1].data["status"] == RunStatus.FAILED.value
    assert await run_lease.is_alive("run-pre-fail") is False
    assert conversation_lease.released is True


async def test_realtime_runner_timeout_releases_leases_and_returns_failed(monkeypatch):
    from app.core.enums import RunStatus
    from app.core.events import EventType
    from app.core.metrics import InMemoryMetrics
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner
    from app.tasks import run_store

    class _Orchestrator:
        async def run(self, **kwargs):
            await asyncio.sleep(0.05)
            return "too late"

    failed_runs: list[tuple[str, str]] = []

    async def _mark_failed(run_id: str, error: str) -> None:
        failed_runs.append((run_id, error))

    class _EventBus:
        def __init__(self) -> None:
            self.events = []

        async def publish(self, channel, event):
            self.events.append((channel, event))
            return event

    monkeypatch.setattr(run_store, "mark_run_failed", _mark_failed)
    metrics = InMemoryMetrics()
    event_bus = _EventBus()
    run_lease = FakeRunLease()
    conversation_lease = FakeLockLease("conv-1")
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=run_lease,
        runner_id="runner-test",
        heartbeat_interval_s=0,
        max_runtime_s=0.001,
        metrics=metrics,
        event_bus=event_bus,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-timeout",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.FAILED
    assert result.error == "RUN_TIMEOUT"
    assert failed_runs == [("run-timeout", "RUN_TIMEOUT")]
    assert event_bus.events[0][0] == "run:run-timeout"
    assert event_bus.events[0][1].type is EventType.ERROR
    assert event_bus.events[0][1].data["error"] == "RUN_TIMEOUT"
    assert event_bus.events[1][1].type is EventType.RUN_COMPLETED
    assert event_bus.events[1][1].data["status"] == RunStatus.FAILED.value
    assert await run_lease.is_alive("run-timeout") is False
    assert conversation_lease.released is True
    assert metrics.counters["runner_timeouts_total"]


async def test_realtime_runner_updates_active_run_metric():
    from app.core.metrics import InMemoryMetrics
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner

    class _Orchestrator:
        async def run(self, **kwargs):
            return "answer"

    metrics = InMemoryMetrics()
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=FakeRunLease(),
        runner_id="runner-test",
        heartbeat_interval_s=0,
        metrics=metrics,
    )

    await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        )
    )

    assert metrics.gauges["runner_active_runs"][-1][0] == 0


async def test_realtime_runner_renews_conversation_lock_with_heartbeat():
    from app.core.enums import RunStatus
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner

    class _Orchestrator:
        async def run(self, **kwargs):
            await asyncio.sleep(0.035)
            return "answer"

    conversation_lease = FakeLockLease("conv-1")
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=FakeRunLease(),
        runner_id="runner-test",
        heartbeat_interval_s=0.01,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert conversation_lease.renew_count > 0
    assert conversation_lease.released is True


async def test_realtime_runner_releases_leases_when_heartbeat_renew_fails():
    from app.core.enums import RunStatus
    from app.runtime.runner import RealtimeRunRequest, RealtimeRunner

    class _FailingRunLease(FakeRunLease):
        async def renew(self, run_id: str) -> None:
            raise RuntimeError("redis transient")

    class _Orchestrator:
        async def run(self, **kwargs):
            await asyncio.sleep(0.025)
            return "answer"

    run_lease = _FailingRunLease()
    conversation_lease = FakeLockLease("conv-1")
    conversation_lease.fail_renew = True
    runner = RealtimeRunner(
        orchestrator_factory=lambda: _Orchestrator(),
        run_lease=run_lease,
        runner_id="runner-test",
        heartbeat_interval_s=0.01,
    )

    result = await runner.run_chat(
        RealtimeRunRequest(
            agent_run_id="run-1",
            conversation_id="conv-1",
            user_id="user-1",
            trace_id="trace-1",
            message="hello",
            metadata={},
            accepted_at=0.0,
        ),
        conversation_lease=conversation_lease,
    )

    assert result.status is RunStatus.SUCCEEDED
    assert await run_lease.is_alive("run-1") is False
    assert conversation_lease.released is True


async def test_realtime_runner_capacity_reservation_is_bounded():
    from app.runtime.runner import RealtimeRunner

    runner = RealtimeRunner(max_concurrency=1, heartbeat_interval_s=0)

    slot = runner.try_acquire_capacity()

    assert slot is not None
    assert runner.try_acquire_capacity() is None
    await slot.release()
    assert runner.try_acquire_capacity() is not None
