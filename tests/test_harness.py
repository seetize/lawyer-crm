import json
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_project_harness_contract_is_complete() -> None:
    required = [
        ROOT / "AGENTS.md",
        ROOT / ".codex/config.toml",
        ROOT / ".codex/schemas/result.schema.json",
        ROOT / ".harness/project.yaml",
        ROOT / ".harness/memory/architecture.md",
        ROOT / ".harness/memory/decisions.md",
        ROOT / ".harness/memory/failures.md",
        ROOT / "scripts/harness.ps1",
        ROOT / "scripts/harness_runtime.py",
        ROOT / "scripts/recover_harness.ps1",
        ROOT / "scripts/watchdog.ps1",
        ROOT / "scripts/install_watchdog.ps1",
        ROOT / "scripts/run_bot.py",
        ROOT / ".harness/memory/lessons.json",
        ROOT / "scripts/verify.ps1",
    ]
    assert all(path.is_file() for path in required)

    config = tomllib.loads((ROOT / ".codex/config.toml").read_text("utf-8"))
    assert config["approval_policy"] == "never"
    assert config["sandbox_mode"] == "workspace-write"
    assert config["sandbox_workspace_write"]["network_access"] is True
    assert config["agents"]["max_concurrent_threads_per_session"] <= 2

    schema = json.loads(
        (ROOT / ".codex/schemas/result.schema.json").read_text("utf-8")
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_only_custom_agent_is_independent_read_only_reviewer() -> None:
    agent_directory = ROOT / ".codex/agents"
    configs = {
        path.stem: tomllib.loads(path.read_text("utf-8"))
        for path in agent_directory.glob("*.toml")
    }
    assert set(configs) == {"project_reviewer"}
    for config in configs.values():
        assert config["name"]
        assert config["description"]
        assert config["developer_instructions"]
    assert configs["project_reviewer"]["sandbox_mode"] == "read-only"


def test_unattended_recovery_keeps_security_boundaries() -> None:
    harness = (ROOT / "scripts/harness.ps1").read_text("utf-8")
    watchdog = (ROOT / "scripts/watchdog.ps1").read_text("utf-8")
    installer = (ROOT / "scripts/install_watchdog.ps1").read_text("utf-8")
    combined = "\n".join((harness, watchdog, installer)).casefold()

    assert "beautyinspectorharnesswriter" in harness.casefold()
    assert "convertfrom-securestring" in harness.casefold()
    assert "task.dpapi" in harness
    assert "--ask-for-approval never" in harness
    assert "--sandbox workspace-write" in harness
    assert "run targeted checks only" in harness.casefold()
    assert "wrapper runs the full gate once" in harness.casefold()
    assert "dangerously-bypass" not in combined
    assert "invoke-expression" not in combined
    assert "$recent.count -ge 3" in watchdog.casefold()
    assert "-windowstyle hidden" in watchdog.casefold()
    assert "-runlevel limited" in installer.casefold()
    assert "beautyinspector-harnessrecovery" in installer.casefold()
    assert "[timespan]::zero" in installer.casefold()


def test_lesson_memory_starts_as_non_executable_data() -> None:
    payload = json.loads(
        (ROOT / ".harness/memory/lessons.json").read_text("utf-8")
    )
    assert payload["version"] == 1
    assert isinstance(payload["lessons"], list)


def test_windows_powershell_native_stdin_preserves_unicode() -> None:
    expected = "проверка кириллицы"
    command = (
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "$env:PYTHONIOENCODING='utf-8'; "
        f"'{expected}' | & '{sys.executable}' -c \"import sys; "
        f"print('OK' if sys.stdin.read().lstrip('\\ufeff').strip() == '{expected}' else 'BAD')\""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="ascii",
    )
    assert result.stdout.strip() == "OK"
