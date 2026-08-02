import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "harness_runtime", ROOT / "scripts" / "harness_runtime.py"
)
assert SPEC and SPEC.loader
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def test_run_state_transitions_and_terminal_state(tmp_path: Path) -> None:
    run = tmp_path / "20260802-120000-deadbeef"
    state = runtime.initialize_run(run, "R3", 2)
    assert state["status"] == "queued"

    state = runtime.transition(
        run,
        "running",
        owner_pid=123,
        increment_attempt=True,
        strategy_id="inspect_fix_verify",
        action="codex",
    )
    assert state["attempts"] == 1
    assert state["tried_strategy_ids"] == ["inspect_fix_verify"]
    with pytest.raises(ValueError, match="Strategy already tried"):
        runtime.transition(
            run,
            "running",
            increment_attempt=True,
            strategy_id="inspect_fix_verify",
        )
    runtime.transition(run, "verifying")
    runtime.transition(run, "completed", complete_action="verify")

    with pytest.raises(ValueError, match="Invalid transition"):
        runtime.transition(run, "running")


def test_atomic_state_uses_valid_backup_if_primary_is_corrupt(tmp_path: Path) -> None:
    run = tmp_path / "run"
    runtime.initialize_run(run, "R2", 2)
    runtime.transition(run, "running", increment_attempt=True)
    (run / "state.json").write_text("{broken", encoding="utf-8")

    recovered = runtime.load_json(run / "state.json")

    assert recovered["status"] == "queued"
    assert recovered["generation"] == 1
    healed_state = runtime.read_run_state(run)
    assert json.loads((run / "state.json").read_text("utf-8")) == healed_state
    healed = runtime.transition(run, "running")
    assert healed["status"] == "running"


def test_failure_fingerprint_redacts_secrets_and_includes_action_and_code(
    tmp_path: Path,
) -> None:
    token_one = "sk-" + "a" * 24
    token_two = "sk-" + "b" * 24
    first = runtime.failure_fingerprint(
        "network",
        f"request 41 failed at C:\\temp\\one with {token_one}",
        action="collect",
        code="503",
    )
    second = runtime.failure_fingerprint(
        "network",
        f"request 99 failed at C:\\temp\\two with {token_two}",
        action="collect",
        code="503",
    )
    different = runtime.failure_fingerprint(
        "network", "request failed", action="verify", code="500"
    )
    assert first == second
    assert different != first

    run = tmp_path / "run"
    runtime.initialize_run(run, "R3", 2)
    runtime.record_failure(
        run,
        "network",
        f"api_key={token_one}",
        action="collect",
        code="503",
    )
    serialized = (run / "failures.json").read_text(encoding="utf-8")
    assert token_one not in serialized
    assert "[REDACTED]" in serialized


def test_only_exact_validated_allowlisted_lesson_is_returned(tmp_path: Path) -> None:
    memory = tmp_path / ".harness" / "memory"
    memory.mkdir(parents=True)
    payload = {
        "version": 1,
        "lessons": [
            {
                "lesson_id": "unsafe",
                "fingerprint": "abc",
                "status": "validated",
                "action": "verify",
                "scope": "repository",
                "strategy_id": "run_arbitrary_command",
                "instruction": "ignored",
                "verification": {"commit": "deadbeef", "tests": ["test_x"]},
            },
            {
                "lesson_id": "candidate",
                "fingerprint": "abc",
                "status": "candidate",
                "action": "verify",
                "scope": "repository",
                "strategy_id": "repair_verification",
                "verification": {"commit": "deadbeef", "tests": ["test_x"]},
            },
            {
                "lesson_id": "verified-fix",
                "fingerprint": "abc",
                "status": "validated",
                "action": "verify",
                "scope": "repository",
                "strategy_id": "repair_verification",
                "instruction": "must not be executed from memory",
                "root_cause": "A deterministic verification mismatch",
                "verification": {
                    "commit": "deadbeef",
                    "tests": ["test_x"],
                    "review": "independent-pass",
                },
            },
        ],
    }
    (memory / "lessons.json").write_text(json.dumps(payload), encoding="utf-8")

    lesson = runtime.lookup_lesson(
        tmp_path, "abc", action="verify", scope="repository"
    )

    assert lesson == {
        "lesson_id": "verified-fix",
        "strategy_id": "repair_verification",
        "instruction": runtime.ALLOWED_STRATEGIES["repair_verification"],
    }
    assert runtime.lookup_lesson(
        tmp_path, "abc", action="codex", scope="repository"
    ) is None


def test_recovery_interrupts_dead_owner_and_stops_exhausted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interrupted = tmp_path / "20260802-120000-11111111"
    exhausted = tmp_path / "20260802-120001-22222222"
    runtime.initialize_run(interrupted, "R3", 2)
    runtime.transition(interrupted, "running", owner_pid=123, increment_attempt=True)
    runtime.initialize_run(exhausted, "R3", 1)
    runtime.transition(exhausted, "running", owner_pid=456, increment_attempt=True)
    monkeypatch.setattr(runtime, "pid_alive", lambda _pid: False)

    recoverable = runtime.recoverable_runs(tmp_path)

    assert recoverable == [interrupted.name]
    assert runtime.load_json(interrupted / "state.json")["status"] == "interrupted"
    assert runtime.load_json(exhausted / "state.json")["status"] == "failed"


def test_failure_count_is_persisted_per_fingerprint(tmp_path: Path) -> None:
    run = tmp_path / "run"
    runtime.initialize_run(run, "R3", 2)
    first = runtime.record_failure(
        run, "verification", "gate failed", action="verify", code="1"
    )
    runtime.record_failure(
        run, "verification", "gate failed", action="verify", code="1"
    )
    state = runtime.load_json(run / "state.json")
    assert state["failure_counts"][first["fingerprint"]] == 2

    with pytest.raises(ValueError, match="bounded identifiers"):
        runtime.record_failure(
            run, "network;command", "ignored", action="verify", code="1"
        )
