from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.main import create_app


def test_chat_preflight_is_handled_before_auth():
    client = TestClient(create_app())

    response = client.options(
        "/api/v1/wc2026/chat?user_uuid=cors-user",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code != 401
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed_headers


def test_unauthenticated_chat_post_still_requires_auth_and_includes_cors_headers():
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/wc2026/chat",
        headers={"Origin": "https://frontend.example"},
        json={"message": "hello", "stream": True},
    )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "*"
