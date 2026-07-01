from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.events import AgentEvent, EventType
from tests.harness_fakes import FakeStreamBus, HarnessEvent


async def test_stream_replay_starts_after_last_event_id():
    bus = FakeStreamBus()
    await bus.publish("run-1", HarnessEvent("run-1", "RUN_STARTED"))
    missed = await bus.publish("run-1", HarnessEvent("run-1", "TOKEN", {"token": "A"}))
    await bus.publish("run-1", HarnessEvent("run-1", "TOKEN", {"token": "B"}))

    replayed = [event async for event in bus.replay("run-1", missed.stream_id)]

    assert [event.data["token"] for event in replayed] == ["B"]


async def test_stream_router_iter_events_uses_last_event_id_cursor():
    from app.api.routers.stream import _iter_events

    bus = FakeStreamBus()
    await bus.publish("run-1", HarnessEvent("run-1", "TOKEN", {"token": "A"}))
    cursor = (
        await bus.publish("run-1", HarnessEvent("run-1", "TOKEN", {"token": "B"}))
    ).stream_id
    await bus.publish("run-1", HarnessEvent("run-1", "RUN_COMPLETED", {"status": "SUCCEEDED"}))

    replayed = [event async for event in _iter_events(bus, "run-1", cursor)]

    assert [event.type for event in replayed] == ["RUN_COMPLETED"]


async def test_stream_router_converts_retention_gap_to_stable_error():
    from app.api.routers.stream import _iter_events
    from app.core.events import EventType
    from app.core.metrics import reset_default_metrics_registry

    reset_default_metrics_registry()
    bus = FakeStreamBus()
    bus.gap_before_id = "5-0"

    replayed = [event async for event in _iter_events(bus, "run-1", "1-0")]

    assert len(replayed) == 1
    assert replayed[0].type is EventType.ERROR
    assert replayed[0].data["error"] == "STREAM_GAP"
    assert replayed[0].data["last_event_id"] == "1-0"


async def test_stream_owner_mismatch_raises_403():
    from app.api.routers.stream import _assert_run_owner

    class _Run:
        conversation_id = "conv-1"

    class _Conversation:
        user_id = "owner-1"

    class _Repos:
        async def get_run(self, run_id):
            return _Run()

        async def get_conversation(self, conversation_id):
            return _Conversation()

    with pytest.raises(HTTPException) as exc:
        await _assert_run_owner("run-1", "other-user", _Repos())

    assert exc.value.status_code == 403


async def test_wc2026_chat_stream_route_is_only_sse_route():
    from app.api import deps
    from app.api.main import create_app

    class _Repos:
        async def get_run(self, run_id):
            return SimpleNamespace(conversation_id="conv-1")

        async def get_conversation(self, conversation_id):
            return SimpleNamespace(user_id="owner-1")

    class _ExplodingRateLimiter:
        async def check(self, user_id):
            raise AssertionError("SSE stream route must not be rate limited")

    async def override_repos():
        yield _Repos()

    app = create_app()
    app.dependency_overrides[deps.get_repos] = override_repos
    app.state.rate_limiter = _ExplodingRateLimiter()
    bus = FakeStreamBus()
    app.state.event_bus = bus
    await bus.publish(
        "run-route-1",
        AgentEvent(
            agent_run_id="run-route-1",
            trace_id="trace-route",
            type=EventType.RUN_COMPLETED,
            seq=1,
            data={"status": "SUCCEEDED"},
        ),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        current = await client.get(
            "/api/v1/wc2026/chat/stream/run-route-1?user_uuid=owner-1",
        )
        legacy = await client.get(
            "/api/v1/wc2026/stream/run-route-1?user_uuid=owner-1",
        )

    assert current.status_code == 200
    assert "RUN_COMPLETED" in current.text
    assert legacy.status_code == 404
