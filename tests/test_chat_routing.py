from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.core.enums import RunStatus
from app.core.models import Conversation, Message
from app.core.schemas import ChatRequest


def test_chat_routing_harness_can_represent_route_metadata():
    metadata = {"mode": "auto", "task_type": "chat"}

    assert metadata["mode"] in {"auto", "realtime", "batch"}
    assert metadata["task_type"] == "chat"


def test_auto_route_selects_realtime_for_normal_chat():
    from app.api.routers.chat import select_route_type

    assert select_route_type({"task_type": "chat"}, runtime_mode="auto") == "realtime"


def test_auto_route_selects_batch_for_file_or_slow_tasks():
    from app.api.routers.chat import select_route_type

    assert select_route_type({"task_type": "file_analysis"}, runtime_mode="auto") == "batch"
    assert select_route_type({"task_type": "slow_tool"}, runtime_mode="auto") == "batch"


def test_runtime_mode_celery_forces_batch_route():
    from app.api.routers.chat import select_route_type

    assert select_route_type({"mode": "realtime"}, runtime_mode="celery") == "batch"


def test_accepted_response_preserves_existing_fields_and_adds_route_type():
    from app.api.routers.chat import _accepted

    accepted = _accepted("conv-1", "run-1", "trace-1", route_type="realtime")

    assert accepted.conversation_id == "conv-1"
    assert accepted.agent_run_id == "run-1"
    assert accepted.route_type == "realtime"


async def test_chat_returned_conversation_id_can_fetch_detail():
    from app.api import deps
    from app.api.main import create_app
    from app.api.repos import Repos

    class _MemorySession:
        def __init__(self) -> None:
            self.rows = {}

        async def get(self, model, key):
            return self.rows.get((model, key))

        def add(self, entity) -> None:
            self.rows[(type(entity), entity.id)] = entity

        async def flush(self) -> None:
            return None

        async def refresh(self, entity) -> None:
            now = datetime.now(UTC)
            if isinstance(entity, Conversation):
                entity.created_at = entity.created_at or now
                entity.updated_at = entity.updated_at or now
            if isinstance(entity, Message):
                entity.created_at = entity.created_at or now
                entity.meta = entity.meta or {}

        async def commit(self) -> None:
            return None

    class _MemoryRepos(Repos):
        async def get_conversation_with_messages(self, conversation_id):
            conversation = await self.get_conversation(conversation_id)
            if conversation is None:
                return None
            conversation.messages = [
                entity
                for (model, _), entity in self.session.rows.items()
                if model is Message and entity.conversation_id == conversation_id
            ]
            return conversation

    class _Lease:
        async def renew(self):
            return True

        async def release(self):
            return True

    class _Lock:
        async def acquire(self, *args, **kwargs):
            return _Lease()

    class _CapacitySlot:
        async def release(self):
            return None

    class _Runner:
        def try_acquire_capacity(self):
            return _CapacitySlot()

        async def run_chat(self, request, *, conversation_lease=None, capacity_slot=None):
            if conversation_lease is not None:
                await conversation_lease.release()
            if capacity_slot is not None:
                await capacity_slot.release()

    app = create_app()
    app.state.conversation_lock = _Lock()
    app.state.realtime_runner = _Runner()
    repos = _MemoryRepos(_MemorySession())

    async def override_repos():
        yield repos

    app.dependency_overrides[deps.get_repos] = override_repos
    headers = {"Authorization": "Bearer user-conversation-detail"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            "/chat",
            headers=headers,
            json={"message": "hello"},
        )
        assert resp.status_code == 202
        conversation_id = resp.json()["conversation_id"]

        detail = await client.get(
            f"/conversations/{conversation_id}",
            headers=headers,
        )

    assert detail.status_code == 200
    assert detail.json()["id"] == conversation_id
    assert detail.json()["messages"][0]["content"] == "hello"


async def test_duplicate_idempotency_claim_replays_before_conversation_lock():
    from app.api.routers import chat
    from app.api.idempotency import chat_request_hash

    request_hash = chat_request_hash(
        message="hello",
        conversation_id="conv-1",
        metadata={"mode": "realtime"},
    )

    class _ExistingRecord:
        def __init__(self) -> None:
            self.request_hash = request_hash
            self.agent_run_id = "run-existing"
            self.response = {
                "conversation_id": "conv-1",
                "agent_run_id": "run-existing",
                "trace_id": "trace-existing",
                "status": "PENDING",
                "stream_url": "/stream/run-existing",
                "ws_url": "/ws/run-existing",
                "route_type": "realtime",
            }

    class _Repos:
        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def claim_idempotency_record(self, **kwargs):
            return _ExistingRecord(), False

        async def get_run(self, run_id):
            return SimpleNamespace(status=RunStatus.RUNNING)

    class _Lock:
        async def acquire(self, *args, **kwargs):
            raise AssertionError("lock must not be acquired for idempotency replay")

    monkey_state = SimpleNamespace(conversation_lock=_Lock())
    request = SimpleNamespace(
        headers={"idempotency-key": "key-1"},
        state=SimpleNamespace(trace_id="trace-new"),
        app=SimpleNamespace(state=monkey_state),
    )

    response = await chat.create_chat(
        ChatRequest(
            message="hello",
            conversation_id="conv-1",
            metadata={"mode": "realtime"},
        ),
        request,
        "user-1",
        _Repos(),
    )

    assert response.agent_run_id == "run-existing"
    assert response.status is RunStatus.RUNNING


async def test_stream_false_is_rejected_before_side_effects():
    from app.api.routers import chat

    class _Repos:
        async def get_idempotency_record(self, *args, **kwargs):
            raise AssertionError("repositories must not be touched")

    class _Lock:
        async def acquire(self, *args, **kwargs):
            raise AssertionError("lock must not be acquired")

    request = SimpleNamespace(
        headers={"idempotency-key": "key-sync"},
        state=SimpleNamespace(trace_id="trace-sync"),
        app=SimpleNamespace(state=SimpleNamespace(conversation_lock=_Lock())),
    )

    with pytest.raises(Exception) as exc:
        await chat.create_chat(
            ChatRequest(
                message="hello",
                stream=False,
                metadata={"mode": "realtime"},
            ),
            request,
            "user-1",
            _Repos(),
        )

    assert getattr(exc.value, "status_code", None) == 422
    assert getattr(exc.value, "detail", None) == "STREAM_FALSE_NOT_SUPPORTED"


async def test_forbidden_conversation_does_not_reserve_realtime_capacity():
    from app.api.routers import chat

    class _Repos:
        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def get_conversation(self, conversation_id):
            return SimpleNamespace(id=conversation_id, user_id="other-user")

    class _Runner:
        def __init__(self) -> None:
            self.reserve_calls = 0

        def try_acquire_capacity(self):
            self.reserve_calls += 1
            raise AssertionError("capacity must not be reserved before owner check")

    runner = _Runner()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-1"),
        app=SimpleNamespace(state=SimpleNamespace(realtime_runner=runner)),
    )

    with pytest.raises(Exception) as exc:
        await chat.create_chat(
            ChatRequest(
                message="hello",
                conversation_id="conv-other",
                metadata={"mode": "realtime"},
            ),
            request,
            "user-1",
            _Repos(),
        )

    assert getattr(exc.value, "status_code", None) == 403
    assert runner.reserve_calls == 0


async def test_dispatch_realtime_keeps_strong_reference_until_task_done():
    from app.api.routers import chat

    started = asyncio.Event()
    finish = asyncio.Event()

    class _Runner:
        async def run_chat(self, request, *, conversation_lease=None, capacity_slot=None):
            started.set()
            await finish.wait()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime_runner=_Runner())))
    payload = {
        "agent_run_id": "run-bg-1",
        "conversation_id": "conv-1",
        "trace_id": "trace-1",
        "message": "hello",
        "metadata": {},
    }

    task = chat._dispatch_realtime(request, payload, "user-1", None)
    await asyncio.wait_for(started.wait(), timeout=1)

    assert task in chat._BACKGROUND_TASKS
    finish.set()
    await asyncio.wait_for(task, timeout=1)
    assert task not in chat._BACKGROUND_TASKS


def test_missing_realtime_runner_fails_closed_instead_of_creating_fallback():
    from app.api.routers import chat

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(Exception) as exc:
        chat._get_realtime_runner(request)

    assert getattr(exc.value, "status_code", None) == 503


async def test_realtime_explicit_provider_limit_returns_429_without_run():
    from app.api.routers import chat
    from app.core.config import Settings

    class _Limiter:
        async def check(self, request):
            return SimpleNamespace(
                allowed=False,
                reason="RATE_LIMITED",
                retry_after_ms=2500,
            )

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(provider_limiter=_Limiter()))
    )

    with pytest.raises(Exception) as exc:
        await chat._apply_provider_preflight(
            ChatRequest(message="hello", metadata={"mode": "realtime"}),
            request,
            "realtime",
            settings=Settings(
                _env_file=None,
                llm_provider="openai",
                openai_api_key="sk-test",
            ),
            user_id="user-1",
        )

    assert getattr(exc.value, "status_code", None) == 429
    assert getattr(exc.value, "headers", {}).get("Retry-After") == "3"


async def test_auto_mode_provider_limit_degrades_to_batch():
    from app.api.routers import chat
    from app.core.config import Settings

    class _Limiter:
        async def check(self, request):
            return SimpleNamespace(
                allowed=False,
                reason="RATE_LIMITED",
                retry_after_ms=1000,
            )

    body = ChatRequest(message="hello", metadata={"mode": "auto"})
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(provider_limiter=_Limiter()))
    )

    route = await chat._apply_provider_preflight(
        body,
        request,
        "realtime",
        settings=Settings(
            _env_file=None,
            llm_provider="openai",
            openai_api_key="sk-test",
        ),
        user_id="user-1",
    )

    assert route == "batch"
    assert body.metadata["degraded"] is True
    assert body.metadata["degraded_reason"] == "provider_rate_limited"
