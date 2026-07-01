#!/usr/bin/env python3
"""Plan and optionally execute DockerHost release operations.

The default mode is a dry run that prints a redacted audit JSON plan. Passing
--execute is required before this script invokes envctl, git, or curl.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, TextIO
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GIT_SUBDIR = "dockerhost"
WC2026_API_PREFIX = "/api/v1/wc2026"
SECRET_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*)"
    r"\s*[:=]\s*([^\s,\"']+)"
)
SENSITIVE_JSON_FIELD_RE = re.compile(
    r'(?i)("?[A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)[A-Z0-9_]*"?)'
    r"\s*:\s*"
    r'("[^"]+"|\'[^\']+\')'
)
BEARER_RE = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+=*")
OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b")
AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{12,}\b")
SLACK_BOT_TOKEN_RE = re.compile(r"\bxoxb-[A-Za-z0-9-]{6,}\b")
PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
    re.DOTALL,
)

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Secret:
    mode: str
    name: str
    path: str | None = None

    def envctl_args(self) -> list[str]:
        if self.mode == "secret-env":
            return ["--secret-env", self.name]
        return ["--secret-file", f"{self.name}={self.path}"]

    def display_envctl_args(self) -> list[str]:
        if self.mode == "secret-env":
            return ["--secret-env", self.name]
        return ["--secret-file", f"{self.name}=<redacted-secret-file>"]

    def audit_entry(self) -> dict[str, str]:
        return {"mode": self.mode, "name": self.name}


@dataclass(frozen=True)
class Step:
    label: str
    command: list[str]
    display_command: list[str] | None = None
    kind: str = "command"

    def audit_command(self, redactor: "Redactor") -> list[str]:
        command = self.display_command if self.display_command is not None else self.command
        return [redactor.text(part) for part in command]


class UsageError(Exception):
    pass


class Redactor:
    def __init__(self, secrets: list[Secret]) -> None:
        self._secret_file_paths = [secret.path for secret in secrets if secret.path]

    def text(self, value: str | None) -> str:
        if value is None:
            return ""
        redacted = str(value)
        for path in self._secret_file_paths:
            if path:
                redacted = redacted.replace(path, "<redacted-secret-file>")
        redacted = PEM_BLOCK_RE.sub("<redacted-private-key>", redacted)
        redacted = BEARER_RE.sub("Bearer <redacted>", redacted)
        redacted = OPENAI_KEY_RE.sub("sk-<redacted>", redacted)
        redacted = AWS_ACCESS_KEY_RE.sub("AKIA<redacted>", redacted)
        redacted = SLACK_BOT_TOKEN_RE.sub("xoxb-<redacted>", redacted)
        redacted = SENSITIVE_JSON_FIELD_RE.sub(_redact_sensitive_json_field, redacted)
        redacted = SENSITIVE_ASSIGNMENT_RE.sub(_redact_sensitive_assignment, redacted)
        return redacted


def _redact_sensitive_assignment(match: re.Match[str]) -> str:
    name = match.group(1)
    value = match.group(2)
    if value.startswith("<redacted"):
        return f"{name}={value}"
    return f"{name}=<redacted>"


def _redact_sensitive_json_field(match: re.Match[str]) -> str:
    name = match.group(1)
    value = match.group(2)
    quote = value[0]
    return f"{name}: {quote}<redacted>{quote}"


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = _build_parser()
    try:
        with contextlib.redirect_stderr(stderr):
            args = parser.parse_args(argv)
        secrets = _parse_secrets(args)
        steps = _build_steps(args, secrets)
    except SystemExit as exc:
        return int(exc.code)
    except UsageError as exc:
        print(f"error: {exc}", file=stderr)
        return 2

    redactor = Redactor(secrets)
    step_results: list[dict[str, object]]
    if args.execute:
        step_results, exit_code = _execute_steps(
            steps,
            runner or _run_command,
            redactor,
        )
    else:
        step_results = [
            {
                "label": step.label,
                "command": step.audit_command(redactor),
                "status": "planned",
            }
            for step in steps
        ]
        exit_code = 0

    audit = _audit(args, secrets, step_results)
    audit_text = json.dumps(audit, ensure_ascii=False, indent=2)
    if args.audit_json:
        Path(args.audit_json).write_text(audit_text + "\n", encoding="utf-8")
    print(audit_text, file=stdout)
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or execute DockerHost deploy, redeploy, rollback, smoke, and destroy operations."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for action in ("deploy", "redeploy"):
        subparser = subparsers.add_parser(action)
        _add_common_options(subparser)
        _add_git_options(subparser)
        subparser.add_argument("--git-ref", required=True)
        _add_smoke_options(subparser)
        _add_secret_options(subparser)
        _add_execution_options(subparser)

    rollback = subparsers.add_parser("rollback")
    _add_common_options(rollback)
    _add_git_options(rollback)
    rollback.add_argument("--previous-sha", required=True)
    _add_smoke_options(rollback)
    _add_secret_options(rollback)
    _add_execution_options(rollback)

    smoke = subparsers.add_parser("smoke")
    _add_common_options(smoke)
    _add_smoke_options(smoke)
    _add_execution_options(smoke)

    destroy = subparsers.add_parser("destroy")
    destroy.add_argument("--name", required=True)
    destroy.add_argument("--envctl-bin", default="envctl")
    _add_execution_options(destroy)

    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", required=True)
    parser.add_argument("--project-dir", default=str(ROOT))
    parser.add_argument("--envctl-bin", default="envctl")
    parser.add_argument("--git-bin", default="git")
    parser.add_argument("--curl-bin", default="curl")


def _add_git_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--git-url", required=True)
    parser.add_argument("--git-subdir", default=DEFAULT_GIT_SUBDIR)


def _add_smoke_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--smoke-user", default="dockerhost-release-smoke")
    parser.add_argument("--log-tail", type=int, default=200)
    parser.add_argument("--sse-timeout", type=int, default=45)


def _add_secret_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--secret-env", action="append", default=[])
    parser.add_argument("--secret-file", action="append", default=[])


def _add_execution_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--audit-json")


def _parse_secrets(args: argparse.Namespace) -> list[Secret]:
    secrets: list[Secret] = []
    for name in getattr(args, "secret_env", []):
        _validate_secret_name(name, "secret-env")
        secrets.append(Secret(mode="secret-env", name=name))
    for item in getattr(args, "secret_file", []):
        if "=" not in item:
            raise UsageError("secret-file expects KEY=PATH with a secret name and private file path")
        name, path = item.split("=", 1)
        _validate_secret_name(name, "secret-file")
        if not path:
            raise UsageError("secret-file expects KEY=PATH with a non-empty path")
        secrets.append(Secret(mode="secret-file", name=name, path=path))
    return secrets


def _validate_secret_name(name: str, option: str) -> None:
    if "=" in name or not SECRET_NAME_RE.fullmatch(name):
        raise UsageError(
            f"{option} expects an environment variable name, not an inline value"
        )


def _build_steps(args: argparse.Namespace, secrets: list[Secret]) -> list[Step]:
    action = args.action
    if action == "destroy":
        return _destroy_steps(args)
    if action == "smoke":
        return _smoke_steps(args)

    git_ref = args.previous_sha if action == "rollback" else args.git_ref
    up_label = "rollback previous SHA" if action == "rollback" else f"envctl git pull {action}"
    return [
        *_git_preflight_steps(args, git_ref),
        Step(
            "dockerhost project check",
            [args.envctl_bin, "check-project", "--dir", args.project_dir],
        ),
        Step(
            "dockerhost template validation",
            [
                args.envctl_bin,
                "validate-template",
                "--dir",
                str(Path(args.project_dir) / args.git_subdir),
            ],
        ),
        _envctl_up_step(args, git_ref, secrets, up_label),
        *_smoke_steps(args),
    ]


def _git_preflight_steps(args: argparse.Namespace, git_ref: str) -> list[Step]:
    return [
        Step("git status preflight", [args.git_bin, "-C", args.project_dir, "status", "--short"]),
        Step("git head preflight", [args.git_bin, "-C", args.project_dir, "rev-parse", "HEAD"]),
        Step("git remote ref preflight", [args.git_bin, "ls-remote", args.git_url, git_ref]),
    ]


def _envctl_up_step(
    args: argparse.Namespace,
    git_ref: str,
    secrets: list[Secret],
    label: str,
) -> Step:
    command = [
        args.envctl_bin,
        "up",
        "--name",
        args.name,
        "--git-url",
        args.git_url,
        "--git-ref",
        git_ref,
        "--git-subdir",
        args.git_subdir,
    ]
    display_command = list(command)
    for secret in secrets:
        command.extend(secret.envctl_args())
        display_command.extend(secret.display_envctl_args())
    return Step(label, command, display_command)


def _smoke_steps(args: argparse.Namespace) -> list[Step]:
    base_url = args.base_url.rstrip("/")
    chat_url = _wc2026_url(base_url, "/chat", args.smoke_user)
    run_status_url = _wc2026_url(base_url, "/runs/{agent_run_id}", args.smoke_user)
    display_run_status_url = _wc2026_url(base_url, "/runs/<agent-run-id>", args.smoke_user)
    chat_body = json.dumps(
        {
            "message": "smoke: DockerHost async chat connectivity",
            "stream": True,
            "metadata": {"release_smoke": True},
            "wc2026_context": _smoke_wc2026_context(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    stream_false_body = json.dumps(
        {"message": "smoke: stream=false must be rejected", "stream": False},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return [
        Step("envctl status", [args.envctl_bin, "status", "--name", args.name]),
        Step("healthz", [args.curl_bin, "-fsS", f"{base_url}/healthz"]),
        Step("readyz", [args.curl_bin, "-fsS", f"{base_url}/readyz"]),
        Step(
            "stream=false 422 smoke",
            [
                args.curl_bin,
                "-sS",
                "-w",
                "\n%{http_code}",
                "-H",
                "Content-Type: application/json",
                "-d",
                stream_false_body,
                chat_url,
            ],
            kind="stream_false",
        ),
        Step(
            "accepted chat smoke",
            [
                args.curl_bin,
                "-fsS",
                "-H",
                "Content-Type: application/json",
                "-d",
                chat_body,
                chat_url,
            ],
            kind="chat_accept",
        ),
        Step(
            "sse smoke",
            [
                args.curl_bin,
                "-fsS",
                "-N",
                "--max-time",
                str(args.sse_timeout),
                f"{base_url}{{stream_url}}",
            ],
            display_command=[
                args.curl_bin,
                "-fsS",
                "-N",
                "--max-time",
                str(args.sse_timeout),
                f"{base_url}<stream-url-from-chat>",
            ],
            kind="sse",
        ),
        Step(
            "run status",
            [
                args.curl_bin,
                "-fsS",
                run_status_url,
            ],
            display_command=[
                args.curl_bin,
                "-fsS",
                display_run_status_url,
            ],
        ),
        Step(
            "worker logs",
            [
                args.envctl_bin,
                "logs",
                "--name",
                args.name,
                "--service",
                "worker",
                "--tail",
                str(args.log_tail),
            ],
        ),
        Step(
            "reaper logs",
            [
                args.envctl_bin,
                "logs",
                "--name",
                args.name,
                "--service",
                "reaper",
                "--tail",
                str(args.log_tail),
            ],
        ),
    ]


def _wc2026_url(base_url: str, path: str, user_uuid: str) -> str:
    encoded_user = quote(user_uuid, safe="")
    return f"{base_url}{WC2026_API_PREFIX}{path}?user_uuid={encoded_user}"


def _smoke_wc2026_context() -> dict[str, object]:
    return {
        "current_match_id": "83",
        "current_match": {
            "id": "83",
            "fd_match_id": "83",
            "description": "Mexico vs Ecuador",
            "stage": "group",
            "stage_label": "Group",
            "home": {"name": "Mexico", "short_name": "MEX"},
            "away": {"name": "Ecuador", "short_name": "ECU"},
            "is_unlocked": True,
        },
        "entitlements": {
            "has_all": False,
            "unlocked_matches": ["83"],
            "locked_matches": [],
        },
    }


def _destroy_steps(args: argparse.Namespace) -> list[Step]:
    return [
        Step("unexpose db", [args.envctl_bin, "unexpose", "--name", args.name, "--service", "db"]),
        Step(
            "unexpose cache",
            [args.envctl_bin, "unexpose", "--name", args.name, "--service", "cache"],
        ),
        Step("destroy disposable environment", [args.envctl_bin, "down", "--name", args.name]),
    ]


def _execute_steps(
    steps: list[Step],
    runner: Runner,
    redactor: Redactor,
) -> tuple[list[dict[str, object]], int]:
    context: dict[str, str] = {}
    results: list[dict[str, object]] = []
    failed = False
    stop_after_run_status = False

    for step in steps:
        if failed and not (stop_after_run_status and step.label == "run status"):
            results.append(
                {
                    "label": step.label,
                    "command": step.audit_command(redactor),
                    "status": "skipped",
                }
            )
            continue

        command = _resolve_command(step.command, context)
        display_command = _resolve_command(
            step.display_command if step.display_command is not None else step.command,
            context,
        )
        completed = runner(command)
        stdout = redactor.text(completed.stdout or "")
        stderr = redactor.text(completed.stderr or "")
        validation_error = _validate_completed_step(step, completed, stdout, context)
        status = "passed" if completed.returncode == 0 and validation_error is None else "failed"
        if status == "failed":
            if step.kind == "sse":
                stop_after_run_status = True
            else:
                failed = True
        elif stop_after_run_status and step.label == "run status":
            failed = True
        results.append(
            {
                "label": step.label,
                "command": [redactor.text(part) for part in display_command],
                "status": status,
                "returncode": completed.returncode,
                "stdout": stdout[:2000],
                "stderr": stderr[:2000],
                **({"error": validation_error} if validation_error else {}),
            }
        )

    return results, 1 if failed else 0


def _validate_completed_step(
    step: Step,
    completed: subprocess.CompletedProcess[str],
    stdout: str,
    context: dict[str, str],
) -> str | None:
    if completed.returncode != 0:
        return None
    if step.kind == "stream_false":
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        status_code = lines[-1] if lines else ""
        body = "\n".join(lines[:-1])
        if status_code != "422" or "STREAM_FALSE_NOT_SUPPORTED" not in body:
            return "stream=false smoke expected 422 STREAM_FALSE_NOT_SUPPORTED"
    elif step.kind == "chat_accept":
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return "accepted chat smoke did not return JSON"
        run_id = payload.get("agent_run_id")
        if not isinstance(run_id, str) or not run_id:
            return "accepted chat smoke missing agent_run_id"
        stream_url = payload.get("stream_url")
        if not isinstance(stream_url, str) or not stream_url.startswith("/"):
            return "accepted chat smoke missing stream_url"
        if not stream_url.startswith(f"{WC2026_API_PREFIX}/chat/stream/"):
            return "accepted chat smoke returned non-WC2026 stream_url"
        if "user_uuid=" not in stream_url:
            return "accepted chat smoke stream_url missing user_uuid"
        context["agent_run_id"] = run_id
        context["stream_url"] = stream_url
        conversation_id = payload.get("conversation_id")
        if isinstance(conversation_id, str):
            context["conversation_id"] = conversation_id
    elif step.kind == "sse":
        if "RUN_COMPLETED" in stdout:
            return None
        if "RUN_FAILED" in stdout or "RUN_CANCELLED" in stdout:
            return "sse smoke observed failed or cancelled terminal event"
        else:
            return "sse smoke did not observe a terminal event"
    return None


def _resolve_command(command: list[str], context: dict[str, str]) -> list[str]:
    resolved: list[str] = []
    for part in command:
        resolved.append(
            part.replace("{stream_url}", context.get("stream_url", "<stream-url-from-chat>"))
            .replace("{agent_run_id}", context.get("agent_run_id", "<agent-run-id>"))
        )
    return resolved


def _audit(
    args: argparse.Namespace,
    secrets: list[Secret],
    steps: list[dict[str, object]],
) -> dict[str, object]:
    git_ref = None
    previous_sha = getattr(args, "previous_sha", None)
    if args.action in {"deploy", "redeploy"}:
        git_ref = args.git_ref
    elif args.action == "rollback":
        git_ref = previous_sha

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "action": args.action,
        "execute": bool(args.execute),
        "env_name": args.name,
        "project_dir": getattr(args, "project_dir", None),
        "git_url": getattr(args, "git_url", None),
        "git_ref": git_ref,
        "previous_sha": previous_sha,
        "git_subdir": getattr(args, "git_subdir", None),
        "base_url": getattr(args, "base_url", None),
        "secrets": [secret.audit_entry() for secret in secrets],
        "steps": steps,
    }


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
