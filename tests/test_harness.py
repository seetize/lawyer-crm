import json
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
        ROOT / "scripts/verify.ps1",
    ]
    assert all(path.is_file() for path in required)

    config = tomllib.loads((ROOT / ".codex/config.toml").read_text("utf-8"))
    assert config["approval_policy"] == "never"
    assert config["sandbox_mode"] == "workspace-write"
    assert config["sandbox_workspace_write"]["network_access"] is True
    assert config["agents"]["max_concurrent_threads_per_session"] <= 3

    schema = json.loads(
        (ROOT / ".codex/schemas/result.schema.json").read_text("utf-8")
    )
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_custom_agents_are_bounded_and_review_is_read_only() -> None:
    agent_directory = ROOT / ".codex/agents"
    configs = {
        path.stem: tomllib.loads(path.read_text("utf-8"))
        for path in agent_directory.glob("*.toml")
    }
    assert {
        "project_explorer",
        "project_implementer",
        "project_reviewer",
        "project_verifier",
    } <= configs.keys()
    for config in configs.values():
        assert config["name"]
        assert config["description"]
        assert config["developer_instructions"]
    assert configs["project_reviewer"]["sandbox_mode"] == "read-only"
    assert configs["project_verifier"]["sandbox_mode"] == "read-only"
