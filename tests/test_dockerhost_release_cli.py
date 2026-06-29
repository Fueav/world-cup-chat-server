from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from scripts import dockerhost_release


def test_deploy_dry_run_plans_ordered_release_steps_without_execution():
    audit = _run_json(
        [
            "deploy",
            "--name",
            "chris-world-cup-chat-server",
            "--git-url",
            "git@github.com:Fueav/world-cup-chat-server.git",
            "--git-ref",
            "abc1234",
            "--base-url",
            "https://api.example.test",
            "--secret-env",
            "ZAI_API_KEY",
            "--secret-file",
            "GEMINI_API_KEY=/Users/chris/.secrets/gemini-key.txt",
        ]
    )

    assert audit["action"] == "deploy"
    assert audit["execute"] is False
    assert audit["env_name"] == "chris-world-cup-chat-server"
    assert audit["secrets"] == [
        {"mode": "secret-env", "name": "ZAI_API_KEY"},
        {"mode": "secret-file", "name": "GEMINI_API_KEY"},
    ]

    labels = [step["label"] for step in audit["steps"]]
    assert labels[:5] == [
        "git status preflight",
        "git head preflight",
        "git remote ref preflight",
        "dockerhost project check",
        "dockerhost template validation",
    ]
    assert labels.index("stream=false 422 smoke") < labels.index("sse smoke")
    assert labels[-2:] == ["worker logs", "reaper logs"]

    deploy_step = _step(audit, "envctl git pull deploy")
    assert deploy_step["command"][:2] == ["envctl", "up"]
    assert "--git-subdir" in deploy_step["command"]
    assert "dockerhost" in deploy_step["command"]
    assert "--secret-env" in deploy_step["command"]
    assert "ZAI_API_KEY" in deploy_step["command"]
    assert "GEMINI_API_KEY=<redacted-secret-file>" in deploy_step["command"]
    assert "/Users/chris/.secrets/gemini-key.txt" not in json.dumps(audit)


def test_execute_mode_runs_commands_with_real_secret_file_path_but_redacted_audit(
    tmp_path: Path,
):
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "stream=false must be rejected" in " ".join(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"error_code":"STREAM_FALSE_NOT_SUPPORTED"}\n422',
                stderr="",
            )
        if command[:2] == ["curl", "-fsS"] and command[-1].endswith("/chat"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "agent_run_id": "run-123",
                        "conversation_id": "conv-123",
                        "stream_url": "/stream/run-123",
                    }
                ),
                stderr="",
            )
        if command[:3] == ["curl", "-fsS", "-N"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="event: TOKEN\ndata: ok\n\nevent: RUN_COMPLETED\ndata: done\n",
                stderr="",
            )
        if command[:2] == ["envctl", "status"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "ZAI_API_KEY=command-output-secret\n"
                    '{"api_key":"json-secret","refresh_token":"json-token"}\n'
                    "Authorization: Bearer raw-token\n"
                    "sk-live-token-value\n"
                    "-----BEGIN PRIVATE KEY-----\nprivate-key-body\n-----END PRIVATE KEY-----"
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    audit_path = tmp_path / "audit.json"
    secret_file = tmp_path / "gemini-key.txt"
    secret_file.write_text("do-not-read-this-secret", encoding="utf-8")

    code, stdout, stderr = _run(
        [
            "redeploy",
            "--name",
            "chris-world-cup-chat-server",
            "--git-url",
            "git@github.com:Fueav/world-cup-chat-server.git",
            "--git-ref",
            "feature/dockerhost",
            "--base-url",
            "https://api.example.test",
            "--secret-env",
            "ZAI_API_KEY",
            "--secret-file",
            f"GEMINI_API_KEY={secret_file}",
            "--execute",
            "--audit-json",
            str(audit_path),
        ],
        runner=fake_runner,
    )

    assert code == 0, stderr
    assert stdout
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["execute"] is True
    assert all(step["status"] == "passed" for step in audit["steps"])

    deploy_call = next(command for command in calls if command[:2] == ["envctl", "up"])
    assert f"GEMINI_API_KEY={secret_file}" in deploy_call
    assert "do-not-read-this-secret" not in stdout
    assert str(secret_file) not in stdout
    assert str(secret_file) not in json.dumps(audit)
    assert "command-output-secret" not in stdout
    assert "json-secret" not in stdout
    assert "json-token" not in stdout
    assert "raw-token" not in stdout
    assert "sk-live-token-value" not in stdout
    assert "BEGIN PRIVATE KEY" not in stdout
    assert "private-key-body" not in stdout


def test_secret_arguments_reject_inline_values_and_audit_only_secret_names():
    code, stdout, stderr = _run(
        [
            "deploy",
            "--name",
            "env",
            "--git-url",
            "git@example.test/repo.git",
            "--git-ref",
            "main",
            "--base-url",
            "https://api.example.test",
            "--secret-env",
            "ZAI_API_KEY=real-secret-value",
        ]
    )

    assert code == 2
    assert not stdout
    assert "secret-env expects an environment variable name" in stderr

    audit = _run_json(
        [
            "deploy",
            "--name",
            "env",
            "--git-url",
            "git@example.test/repo.git",
            "--git-ref",
            "main",
            "--base-url",
            "https://api.example.test",
            "--secret-file",
            "ZAI_API_KEY=/private/path/key.txt",
        ]
    )
    serialized = json.dumps(audit)
    assert "ZAI_API_KEY" in serialized
    assert "/private/path/key.txt" not in serialized
    assert "real-secret-value" not in serialized


def test_execute_queries_run_status_when_sse_lacks_terminal_event():
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "stream=false must be rejected" in " ".join(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"error_code":"STREAM_FALSE_NOT_SUPPORTED"}\n422',
                stderr="",
            )
        if command[:2] == ["curl", "-fsS"] and command[-1].endswith("/chat"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "agent_run_id": "run-456",
                        "conversation_id": "conv-456",
                        "stream_url": "/stream/run-456",
                    }
                ),
                stderr="",
            )
        if command[:3] == ["curl", "-fsS", "-N"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="event: TOKEN\ndata: partial\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    code, stdout, stderr = _run(
        [
            "smoke",
            "--name",
            "env",
            "--base-url",
            "https://api.example.test",
            "--execute",
        ],
        runner=fake_runner,
    )

    assert code == 1, stderr
    audit = json.loads(stdout)
    assert _step(audit, "sse smoke")["status"] == "failed"
    assert _step(audit, "run status")["status"] == "passed"
    assert any(command[-1] == "https://api.example.test/runs/run-456" for command in calls)
    assert _step(audit, "worker logs")["status"] == "skipped"


def test_execute_blocks_release_when_sse_reports_failed_terminal_event():
    calls: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "stream=false must be rejected" in " ".join(command):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"error_code":"STREAM_FALSE_NOT_SUPPORTED"}\n422',
                stderr="",
            )
        if command[:2] == ["curl", "-fsS"] and command[-1].endswith("/chat"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "agent_run_id": "run-failed",
                        "conversation_id": "conv-failed",
                        "stream_url": "/stream/run-failed",
                    }
                ),
                stderr="",
            )
        if command[:3] == ["curl", "-fsS", "-N"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='event: RUN_FAILED\ndata: {"error":"sanitized"}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    code, stdout, stderr = _run(
        [
            "smoke",
            "--name",
            "env",
            "--base-url",
            "https://api.example.test",
            "--execute",
        ],
        runner=fake_runner,
    )

    assert code == 1, stderr
    audit = json.loads(stdout)
    assert _step(audit, "sse smoke")["status"] == "failed"
    assert "failed or cancelled" in _step(audit, "sse smoke")["error"]
    assert _step(audit, "run status")["status"] == "passed"
    assert _step(audit, "worker logs")["status"] == "skipped"
    assert any(command[-1] == "https://api.example.test/runs/run-failed" for command in calls)


def test_rollback_and_destroy_default_to_safe_dry_run_plans():
    rollback = _run_json(
        [
            "rollback",
            "--name",
            "env",
            "--git-url",
            "git@example.test/repo.git",
            "--previous-sha",
            "0123456789abcdef",
            "--base-url",
            "https://api.example.test",
            "--secret-env",
            "ZAI_API_KEY",
        ]
    )

    assert rollback["action"] == "rollback"
    assert rollback["execute"] is False
    assert rollback["git_ref"] == "0123456789abcdef"
    assert rollback["previous_sha"] == "0123456789abcdef"
    rollback_deploy = _step(rollback, "rollback previous SHA")
    assert rollback_deploy["command"][:2] == ["envctl", "up"]
    assert "0123456789abcdef" in rollback_deploy["command"]

    destroy = _run_json(["destroy", "--name", "env"])
    assert destroy["action"] == "destroy"
    assert destroy["execute"] is False
    assert [step["label"] for step in destroy["steps"]] == [
        "unexpose db",
        "unexpose cache",
        "destroy disposable environment",
    ]
    assert _step(destroy, "destroy disposable environment")["command"] == [
        "envctl",
        "down",
        "--name",
        "env",
    ]


def _run_json(argv: list[str]) -> dict[str, object]:
    code, stdout, stderr = _run(argv)
    assert code == 0, stderr
    return json.loads(stdout)


def _run(
    argv: list[str],
    runner: dockerhost_release.Runner | None = None,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = dockerhost_release.main(argv, runner=runner, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _step(audit: dict[str, object], label: str) -> dict[str, object]:
    for step in audit["steps"]:
        if step["label"] == label:
            return step
    raise AssertionError(f"missing step {label}")
