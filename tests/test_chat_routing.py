from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.core.enums import RunStatus
from app.core.models import Conversation, Message
from app.core.schemas import ChatRequest


def _wc_context(match_id: str = "75", *, unlocked: bool = True) -> dict:
    return {
        "current_match_id": match_id,
        "current_match": {
            "id": match_id,
            "fd_match_id": f"fd-{match_id}",
            "description": "阿根廷 vs 法国",
            "stage": "final",
            "stage_label": "决赛",
            "home": {"name": "阿根廷", "short_name": "ARG"},
            "away": {"name": "法国", "short_name": "FRA"},
            "is_unlocked": unlocked,
        },
        "entitlements": {
            "has_all": False,
            "unlocked_matches": [match_id] if unlocked else [],
            "locked_matches": [] if unlocked else [match_id],
        },
    }


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

    accepted = _accepted(
        "conv-1",
        "run-1",
        "trace-1",
        user_uuid="user-url-1",
        route_type="realtime",
    )

    assert accepted.conversation_id == "conv-1"
    assert accepted.agent_run_id == "run-1"
    assert accepted.route_type == "realtime"
    assert (
        accepted.stream_url
        == "/api/v1/wc2026/stream/run-1?user_uuid=user-url-1"
    )
    assert accepted.ws_url == "/api/v1/wc2026/ws/run-1?user_uuid=user-url-1"


def test_wc2026_context_accepts_numeric_fd_match_id_from_upstream_doc():
    request = ChatRequest(
        message="hello",
        wc2026_context={
            **_wc_context("75"),
            "current_match": {
                **_wc_context("75")["current_match"],
                "fd_match_id": 537401,
            },
        },
    )

    assert request.wc2026_context is not None
    assert request.wc2026_context.current_match.fd_match_id == 537401


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
        async def get_wc2026_conversation_id_by_match(self, user_id, match_id):
            return None

        async def get_conversation_wc2026_match_id(self, conversation_id):
            return None

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
    user_uuid = "user-conversation-detail"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        resp = await client.post(
            f"/api/v1/wc2026/chat?user_uuid={user_uuid}",
            json={"message": "hello", "wc2026_context": _wc_context()},
        )
        assert resp.status_code == 202
        payload = resp.json()
        conversation_id = payload["conversation_id"]
        assert payload["stream_url"].startswith("/api/v1/wc2026/stream/")
        assert payload["stream_url"].endswith(f"?user_uuid={user_uuid}")
        assert payload["ws_url"].startswith("/api/v1/wc2026/ws/")
        assert payload["ws_url"].endswith(f"?user_uuid={user_uuid}")

        detail = await client.get(
            f"/api/v1/wc2026/conversations/{conversation_id}?user_uuid={user_uuid}",
        )

    assert detail.status_code == 200
    assert detail.json()["id"] == conversation_id
    assert detail.json()["messages"][0]["content"] == "hello"


async def test_wc2026_chat_requires_url_user_uuid_and_does_not_accept_header_only():
    from app.api.main import create_app

    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        missing = await client.post(
            "/api/v1/wc2026/chat",
            json={"message": "hello"},
        )
        header_only = await client.post(
            "/api/v1/wc2026/chat",
            headers={"Authorization": "Bearer header-user"},
            json={"message": "hello"},
        )

    assert missing.status_code == 401
    assert header_only.status_code == 401


async def test_wc2026_chat_rejects_overlong_user_uuid_before_side_effects():
    from app.api.main import create_app

    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/v1/wc2026/chat?user_uuid={'u' * 65}",
            json={"message": "hello", "wc2026_context": _wc_context()},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "USER_UUID_TOO_LONG"


async def test_wc2026_chat_rejects_overlong_idempotency_key_before_db_claim():
    from app.api.routers import chat

    class _Repos:
        async def get_idempotency_record(self, *args, **kwargs):
            raise AssertionError("repositories must not be touched")

    request = SimpleNamespace(
        headers={"idempotency-key": "k" * 257},
        state=SimpleNamespace(trace_id="trace-overlong-idem"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    with pytest.raises(Exception) as exc:
        await chat.create_chat(
            ChatRequest(
                message="hello",
                metadata={"mode": "realtime"},
                wc2026_context=_wc_context(),
            ),
            request,
            "user-1",
            _Repos(),
        )

    assert getattr(exc.value, "status_code", None) == 422
    assert getattr(exc.value, "detail", None) == "IDEMPOTENCY_KEY_TOO_LONG"


async def test_legacy_chat_route_is_not_available():
    from app.api.main import create_app

    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/chat",
            headers={"Authorization": "Bearer legacy-user"},
            json={"message": "hello"},
        )
        anonymous_response = await client.post(
            "/chat",
            json={"message": "hello"},
        )

    assert response.status_code == 404
    assert anonymous_response.status_code == 404


async def test_duplicate_idempotency_claim_replays_before_conversation_lock():
    from app.api.routers import chat
    from app.api.idempotency import chat_request_hash

    request_hash = chat_request_hash(
        message="hello",
        conversation_id="conv-1",
        metadata={"mode": "realtime"},
        wc2026_context=_wc_context(),
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
                "stream_url": "/api/v1/wc2026/stream/run-existing?user_uuid=user-1",
                "ws_url": "/api/v1/wc2026/ws/run-existing?user_uuid=user-1",
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
                wc2026_context=_wc_context(),
            ),
        request,
        "user-1",
        _Repos(),
    )

    assert response.agent_run_id == "run-existing"
    assert response.status is RunStatus.RUNNING


def test_chat_request_preserves_wc2026_context_and_match_id():
    body = ChatRequest(
        message="hello",
        match_id="75",
        wc2026_context=_wc_context("75"),
    )

    assert body.match_id == "75"
    assert body.wc2026_context.current_match_id == "75"
    assert body.wc2026_context.current_match.id == "75"
    assert body.wc2026_context.current_match.is_unlocked is True


def test_chat_request_hash_changes_when_wc2026_context_changes():
    from app.api.idempotency import chat_request_hash

    first = chat_request_hash(
        message="hello",
        conversation_id="conv-1",
        metadata={"mode": "realtime"},
        wc2026_context=_wc_context("75"),
    )
    second = chat_request_hash(
        message="hello",
        conversation_id="conv-1",
        metadata={"mode": "realtime"},
        wc2026_context=_wc_context("76"),
    )

    assert first != second


async def test_wc2026_chat_requires_trusted_context_before_side_effects():
    from app.api.routers import chat

    class _Repos:
        async def get_idempotency_record(self, *args, **kwargs):
            raise AssertionError("repositories must not be touched")

    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-missing-context"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    with pytest.raises(Exception) as exc:
        await chat.create_chat(
            ChatRequest(message="hello", metadata={"mode": "realtime"}),
            request,
            "user-1",
            _Repos(),
        )

    assert getattr(exc.value, "status_code", None) == 422
    assert getattr(exc.value, "detail", None) == "WC2026_CONTEXT_REQUIRED"


async def test_conversation_cannot_switch_bound_match_before_capacity_reservation():
    from app.api.routers import chat

    class _Repos:
        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def get_conversation(self, conversation_id):
            return SimpleNamespace(id=conversation_id, user_id="user-1")

        async def get_conversation_wc2026_match_id(self, conversation_id):
            return "75"

    class _Runner:
        def try_acquire_capacity(self):
            raise AssertionError("capacity must not be reserved on match conflict")

    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-conflict"),
        app=SimpleNamespace(state=SimpleNamespace(realtime_runner=_Runner())),
    )

    with pytest.raises(Exception) as exc:
        await chat.create_chat(
            ChatRequest(
                message="hello",
                conversation_id="conv-1",
                metadata={"mode": "realtime"},
                wc2026_context=_wc_context("76"),
            ),
            request,
            "user-1",
            _Repos(),
        )

    assert getattr(exc.value, "status_code", None) == 409
    assert getattr(exc.value, "detail", None) == "WC2026_CONVERSATION_MATCH_CONFLICT"


async def test_batch_run_plan_and_payload_include_wc2026_context(monkeypatch):
    from app.api.routers import chat

    captured: dict[str, dict] = {}
    monkeypatch.setattr(
        chat,
        "_dispatch",
        lambda payload: captured.setdefault("payload", payload),
    )

    class _Repos:
        def __init__(self) -> None:
            self.created_plan = None
            self.created_task_payload = None

        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def ensure_conversation(
            self, conversation_id, user, wc2026_match_id=None
        ):
            return SimpleNamespace(id=conversation_id or "conv-created")

        async def add_message(self, **kwargs):
            return None

        async def create_run(self, run_id, conversation_id, trace_id, plan):
            self.created_plan = plan
            return SimpleNamespace(id=run_id)

        async def create_queued_task(self, **kwargs):
            self.created_task_payload = kwargs["payload"]
            return SimpleNamespace(id=kwargs["task_id"])

        async def commit(self):
            return None

    repos = _Repos()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-batch-context"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    response = await chat.create_chat(
        ChatRequest(
            message="hello",
            metadata={"mode": "batch"},
            wc2026_context=_wc_context("75", unlocked=False),
        ),
        request,
        "user-1",
        repos,
    )

    assert response.route_type == "batch"
    assert repos.created_plan["wc2026_context"]["current_match_id"] == "75"
    assert repos.created_task_payload["wc2026_context"]["current_match_id"] == "75"
    assert captured["payload"]["wc2026_context"]["current_match_id"] == "75"


async def test_chat_without_conversation_id_reuses_existing_same_match_conversation(
    monkeypatch,
):
    from app.api.routers import chat

    captured: dict[str, dict] = {}
    monkeypatch.setattr(
        chat,
        "_dispatch",
        lambda payload: captured.setdefault("payload", payload),
    )

    class _Repos:
        def __init__(self) -> None:
            self.created_plan = None
            self.ensure_calls = []

        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def get_wc2026_conversation_id_by_match(self, user_id, match_id):
            assert user_id == "user-1"
            assert match_id == "75"
            return "conv-existing-75"

        async def get_conversation(self, conversation_id):
            assert conversation_id == "conv-existing-75"
            return SimpleNamespace(id=conversation_id, user_id="user-1")

        async def get_conversation_wc2026_match_id(self, conversation_id):
            assert conversation_id == "conv-existing-75"
            return "75"

        async def ensure_conversation(
            self, conversation_id, user, wc2026_match_id=None
        ):
            assert wc2026_match_id == "75"
            self.ensure_calls.append((conversation_id, user))
            return SimpleNamespace(id=conversation_id)

        async def add_message(self, **kwargs):
            return None

        async def create_run(self, run_id, conversation_id, trace_id, plan):
            self.created_plan = plan
            return SimpleNamespace(id=run_id)

        async def create_queued_task(self, **kwargs):
            return SimpleNamespace(id=kwargs["task_id"])

        async def commit(self):
            return None

    repos = _Repos()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-reuse"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )

    response = await chat.create_chat(
        ChatRequest(
            message="hello",
            metadata={"mode": "batch"},
            wc2026_context=_wc_context("75"),
        ),
        request,
        "user-1",
        repos,
    )

    assert response.conversation_id == "conv-existing-75"
    assert repos.ensure_calls == [("conv-existing-75", "user-1")]
    assert repos.created_plan["wc2026_context"]["current_match_id"] == "75"
    assert captured["payload"]["conversation_id"] == "conv-existing-75"


async def test_new_realtime_wc2026_chat_locks_by_user_and_match(monkeypatch):
    from app.api.routers import chat

    monkeypatch.setattr(chat, "_dispatch_realtime", lambda *args, **kwargs: None)

    class _Repos:
        async def get_idempotency_record(self, user_id, idempotency_key):
            return None

        async def get_wc2026_conversation_id_by_match(self, user_id, match_id):
            return None

        async def ensure_conversation(
            self, conversation_id, user, wc2026_match_id=None
        ):
            assert wc2026_match_id == "75"
            return SimpleNamespace(id=conversation_id)

        async def add_message(self, **kwargs):
            return None

        async def create_run(self, run_id, conversation_id, trace_id, plan):
            return SimpleNamespace(id=run_id)

        async def commit(self):
            return None

    class _Lease:
        async def release(self):
            return None

    class _Lock:
        def __init__(self) -> None:
            self.keys = []

        async def acquire(self, key, *args, **kwargs):
            self.keys.append(key)
            return _Lease()

    class _CapacitySlot:
        async def release(self):
            return None

    class _Runner:
        def try_acquire_capacity(self):
            return _CapacitySlot()

    lock = _Lock()
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(trace_id="trace-new-match-lock"),
        app=SimpleNamespace(
            state=SimpleNamespace(conversation_lock=lock, realtime_runner=_Runner())
        ),
    )

    response = await chat.create_chat(
        ChatRequest(
            message="first question",
            metadata={"mode": "realtime"},
            wc2026_context=_wc_context("75"),
        ),
        request,
        "user-1",
        _Repos(),
    )

    assert response.route_type == "realtime"
    assert lock.keys == ["new_wc2026:user-1:75"]


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
                wc2026_context=_wc_context(),
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
    seen_requests = []

    class _Runner:
        async def run_chat(self, request, *, conversation_lease=None, capacity_slot=None):
            seen_requests.append(request)
            started.set()
            await finish.wait()

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime_runner=_Runner())))
    payload = {
        "agent_run_id": "run-bg-1",
        "conversation_id": "conv-1",
        "trace_id": "trace-1",
        "message": "hello",
        "metadata": {},
        "wc2026_context": _wc_context("75"),
    }

    task = chat._dispatch_realtime(request, payload, "user-1", None)
    await asyncio.wait_for(started.wait(), timeout=1)

    assert task in chat._BACKGROUND_TASKS
    assert seen_requests[0].wc2026_context["current_match_id"] == "75"
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
