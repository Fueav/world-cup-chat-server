from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs/specifications/2026-06-23-dockerhost-release-runbook-specification.md"
PLAN_PATH = (
    ROOT
    / "docs/implementation-plans/2026-06-23-dockerhost-release-runbook-implementation-plan.md"
)
RUNBOOK_PATH = ROOT / "docs/DOCKERHOST_RELEASE_RUNBOOK.md"
SCRIPT_PATH = ROOT / "scripts/dockerhost_release.py"

SPEC_ID = "SPEC-DOCKERHOST-RELEASE-RUNBOOK-001"
WORKFLOW_CLASS = "HARNESS-SPEC-FIRST-FEATURE"


def test_spec_and_plan_bind_to_stable_spec_id_and_workflow():
    spec = _read(SPEC_PATH)
    plan = _read(PLAN_PATH)
    runbook = _read(RUNBOOK_PATH)

    _assert_contains_all(
        spec,
        [
            f"Spec ID: `{SPEC_ID}`",
            f"Workflow Class: `{WORKFLOW_CLASS}`",
            "DockerHost Git pull",
            "secret",
            "SSE/WebSocket",
            "rollback to previous SHA",
            "envctl down",
            "scripts/dockerhost_release.py",
            "tests/test_dockerhost_release_cli.py",
            "dry-run",
            "--execute",
        ],
    )
    _assert_contains_all(
        plan,
        [
            f"Spec ID: `{SPEC_ID}`",
            f"Workflow Class: `{WORKFLOW_CLASS}`",
            "docs/DOCKERHOST_RELEASE_RUNBOOK.md",
            "tests/test_dockerhost_release_runbook_contract.py",
            "scripts/dockerhost_release.py",
            "tests/test_dockerhost_release_cli.py",
            ".venv/bin/python -m pytest tests/test_dockerhost_release_cli.py -q",
            ".venv/bin/python -m pytest tests/test_dockerhost_release_runbook_contract.py -q",
            "make verify-release",
        ],
    )
    assert SPEC_ID in runbook


def test_runbook_contains_release_and_rollback_gates():
    runbook = _read(RUNBOOK_PATH)

    _assert_contains_all(
        runbook,
        [
            "envctl version",
            "envctl check-project --dir /Users/chris/AiProject/world-cup-chat-server",
            "envctl validate-template --dir /Users/chris/AiProject/world-cup-chat-server/dockerhost",
            "envctl up",
            "--git-url",
            "--git-ref",
            "--git-subdir dockerhost",
            "--secret-env",
            "--secret-file",
            "/healthz",
            "/readyz",
            "stream=false",
            "422 STREAM_FALSE_NOT_SUPPORTED",
            "SSE Smoke",
            "WebSocket Smoke",
            "envctl logs",
            "--service worker",
            "--service reaper",
            "同环境 Redeploy",
            "回滚到上一 SHA",
            "PREVIOUS_SHA",
            "envctl down --name \"$ENV_NAME\"",
            "审计清单",
        ],
    )


def test_runbook_contains_cli_dry_run_and_execute_boundaries():
    runbook = _read(RUNBOOK_PATH)
    script = _read(SCRIPT_PATH)

    _assert_contains_all(
        runbook,
        [
            "DockerHost Release CLI（默认 dry-run）",
            ".venv/bin/python scripts/dockerhost_release.py deploy",
            ".venv/bin/python scripts/dockerhost_release.py redeploy",
            ".venv/bin/python scripts/dockerhost_release.py rollback --previous-sha",
            ".venv/bin/python scripts/dockerhost_release.py smoke",
            ".venv/bin/python scripts/dockerhost_release.py destroy",
            "--execute",
            "--audit-json",
            "不会调用真实 `git`, `envctl` 或 `curl`",
            "KEY=<redacted-secret-file>",
            "command output 和 audit JSON 都必须脱敏",
        ],
    )
    _assert_contains_all(
        script,
        [
            "deploy",
            "redeploy",
            "rollback",
            "destroy",
            "smoke",
            "--execute",
            "--audit-json",
            "stream=false 422 smoke",
            "rollback previous SHA",
            "destroy disposable environment",
        ],
    )


def test_runbook_preserves_secret_hygiene_contract():
    runbook = _read(RUNBOOK_PATH)
    combined_docs = "\n".join(_read(path) for path in (SPEC_PATH, PLAN_PATH, RUNBOOK_PATH))

    _assert_contains_all(
        runbook,
        [
            "不要打印",
            "ENVCTL_TOKEN",
            "provider key",
            "不要使用会把明文值留在 shell history",
            "只记录 secret 名称",
            "不要记录 secret 值",
        ],
    )

    forbidden_secret_markers = [
        "sk-",
        "sk_live_",
        "AKIA",
        "xoxb-",
        "-----BEGIN",
        "ENVCTL_TOKEN=",
        "provider-key-value",
        "real-secret-value",
    ]
    missing_hygiene = [marker for marker in forbidden_secret_markers if marker in combined_docs]
    assert not missing_hygiene, f"Docs contain secret-like markers: {missing_hygiene}"


def test_runbook_documents_provider_key_pool_secret_file_deploy():
    runbook = _read(RUNBOOK_PATH)

    _assert_contains_all(
        runbook,
        [
            "ZAI_API_KEYS_FILE",
            "PROVIDER_KEY_POOL_FILE",
            "PROVIDER_KEY_POOL_SCOPE=key",
            "--secret-file ZAI_API_KEYS_FILE=<path-to-private-zai-keys-file>",
            "provider_secret=key_pool",
            "provider_key_pool=configured",
        ],
    )


def _read(path: Path) -> str:
    assert path.exists(), f"Missing expected document: {path}"
    return path.read_text(encoding="utf-8")


def _assert_contains_all(text: str, terms: list[str]) -> None:
    missing = [term for term in terms if term not in text]
    assert not missing, f"Missing required terms: {missing}"
