import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"queued", "running", "retry_wait", "verifying", "interrupted"}
TERMINAL_STATUSES = {"completed", "failed"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"retry_wait", "verifying", "interrupted", "failed"},
    "retry_wait": {"running", "failed"},
    "verifying": {"completed", "retry_wait", "interrupted", "failed"},
    "interrupted": {"running", "failed"},
    "completed": set(),
    "failed": set(),
}
OPENAI_SECRET = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")
TELEGRAM_SECRET = re.compile(r"\b[0-9]{8,12}:AA[A-Za-z0-9_-]{25,}\b")
ASSIGNED_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+"
)
BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{12,}")
QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|secret|password)=)[^&#\s]+")
ALLOWED_STRATEGIES = {
    "inspect_fix_verify": (
        "Inspect the existing worktree and failure evidence, form a new root-cause "
        "hypothesis, implement the smallest fix, add a regression test, and verify it."
    ),
    "transient_retry": (
        "Re-check current external availability once, preserving existing work and "
        "without repeating any irreversible side effect."
    ),
    "repair_verification": (
        "Inspect the failed verification evidence and current diff, fix the root cause "
        "without discarding valid work, add or adjust a regression test, and verify it."
    ),
    "recover_interrupted": (
        "Inspect the preserved worktree and run state after interruption, determine the "
        "last completed action, then continue safely from that checkpoint and verify it."
    ),
    "rebuild_process": (
        "Verify the exact project process identity, then recreate only that process "
        "from the documented project command."
    ),
}
LABEL_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        backup = path.with_suffix(path.suffix + ".bak")
        if path.exists():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeError, OSError):
                pass
            else:
                os.replace(path, backup)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path, default: Any = None) -> Any:
    candidates = (path, path.with_suffix(path.suffix + ".bak"))
    errors: list[Exception] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError, OSError) as error:
            errors.append(error)
    if errors:
        raise ValueError(f"No valid JSON state for {path}") from errors[-1]
    return default


def redact(value: str) -> str:
    result = OPENAI_SECRET.sub("[REDACTED]", value)
    result = TELEGRAM_SECRET.sub("[REDACTED]", result)
    result = ASSIGNED_SECRET.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", result
    )
    result = BEARER_SECRET.sub("Bearer [REDACTED]", result)
    result = QUERY_SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", result)
    return result


def normalize_failure(
    category: str,
    message: str,
    *,
    action: str = "unknown",
    code: str = "unknown",
) -> str:
    value = redact(message).casefold()
    value = re.sub(r"[a-z]:\\[^\r\n\t ]+", "<path>", value)
    value = re.sub(r"/[a-z0-9_.\-/]+", "<path>", value)
    value = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "<uuid>",
        value,
    )
    value = re.sub(r"\b\d+\b", "<n>", value)
    value = " ".join(value.split())[:2000]
    return f"{action.casefold()}:{category.casefold()}:{code.casefold()}:{value}"


def failure_fingerprint(
    category: str,
    message: str,
    *,
    action: str = "unknown",
    code: str = "unknown",
) -> str:
    normalized = normalize_failure(category, message, action=action, code=code)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def initialize_run(run_directory: Path, risk: str, max_attempts: int) -> dict[str, Any]:
    state = {
        "version": 1,
        "generation": 1,
        "run_id": run_directory.name,
        "status": "queued",
        "risk": risk,
        "attempts": 0,
        "max_attempts": max(1, max_attempts),
        "owner_pid": None,
        "last_failure_fingerprint": None,
        "last_failure_category": None,
        "failure_counts": {},
        "tried_strategy_ids": [],
        "current_strategy_id": None,
        "current_action": None,
        "completed_actions": [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    atomic_write_json(run_directory / "state.json", state)
    return state


def read_run_state(run_directory: Path, *, heal: bool = True) -> dict[str, Any]:
    path = run_directory / "state.json"
    state = load_json(path)
    if not isinstance(state, dict):
        raise ValueError(f"Missing state: {path}")
    if heal:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeError, OSError):
            atomic_write_json(path, state)
    return state


def transition(
    run_directory: Path,
    status: str,
    *,
    owner_pid: int | None = None,
    increment_attempt: bool = False,
    strategy_id: str | None = None,
    action: str | None = None,
    complete_action: str | None = None,
) -> dict[str, Any]:
    path = run_directory / "state.json"
    state = read_run_state(run_directory)
    current = str(state.get("status"))
    if status != current and status not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"Invalid transition: {current} -> {status}")
    if increment_attempt:
        state["attempts"] = int(state.get("attempts") or 0) + 1
    state["status"] = status
    state["owner_pid"] = owner_pid
    if strategy_id:
        if strategy_id not in ALLOWED_STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy_id}")
        tried = state.setdefault("tried_strategy_ids", [])
        if increment_attempt and strategy_id in tried:
            raise ValueError(f"Strategy already tried in this run: {strategy_id}")
        state["current_strategy_id"] = strategy_id
        if strategy_id not in tried:
            tried.append(strategy_id)
    if action:
        state["current_action"] = action
    if complete_action:
        completed = state.setdefault("completed_actions", [])
        if complete_action not in completed:
            completed.append(complete_action)
    state["generation"] = int(state.get("generation") or 0) + 1
    state["updated_at"] = utc_now()
    atomic_write_json(path, state)
    return state


def record_failure(
    run_directory: Path,
    category: str,
    message: str,
    *,
    action: str,
    code: str,
) -> dict[str, Any]:
    if not LABEL_PATTERN.fullmatch(category) or not LABEL_PATTERN.fullmatch(action):
        raise ValueError("Failure category and action must be bounded identifiers")
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError("Failure code must be a bounded identifier")
    safe_message = redact(message)[:1000]
    fingerprint = failure_fingerprint(
        category,
        safe_message,
        action=action,
        code=code,
    )
    failures_path = run_directory / "failures.json"
    failures = load_json(failures_path, {"version": 1, "generation": 0, "items": []})
    failures["generation"] = int(failures.get("generation") or 0) + 1
    failures["items"].append(
        {
            "fingerprint": fingerprint,
            "category": category,
            "action": action,
            "code": code,
            "summary": safe_message,
            "created_at": utc_now(),
        }
    )
    atomic_write_json(failures_path, failures)

    state_path = run_directory / "state.json"
    state = load_json(state_path)
    state["last_failure_fingerprint"] = fingerprint
    state["last_failure_category"] = category
    counts = state.setdefault("failure_counts", {})
    counts[fingerprint] = int(counts.get(fingerprint) or 0) + 1
    state["generation"] = int(state.get("generation") or 0) + 1
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    return failures["items"][-1]


def lessons_path(root: Path) -> Path:
    return root / ".harness" / "memory" / "lessons.json"


def lookup_lesson(
    root: Path,
    fingerprint: str | None,
    *,
    action: str,
    scope: str,
) -> dict[str, Any] | None:
    if not fingerprint:
        return None
    payload = load_json(lessons_path(root), {"version": 1, "lessons": []})
    for lesson in payload.get("lessons", []):
        verification = lesson.get("verification")
        strategy_id = lesson.get("strategy_id")
        commit = verification.get("commit") if isinstance(verification, dict) else None
        tests = verification.get("tests") if isinstance(verification, dict) else None
        if (
            lesson.get("fingerprint") == fingerprint
            and lesson.get("status") == "validated"
            and lesson.get("action") == action
            and lesson.get("scope") == scope
            and strategy_id in ALLOWED_STRATEGIES
            and isinstance(verification, dict)
            and isinstance(commit, str)
            and re.fullmatch(r"[0-9a-fA-F]{7,40}", commit)
            and isinstance(tests, list)
            and bool(tests)
            and verification.get("review") == "independent-pass"
            and isinstance(lesson.get("root_cause"), str)
            and bool(lesson["root_cause"].strip())
        ):
            return {
                "lesson_id": lesson.get("lesson_id"),
                "strategy_id": strategy_id,
                "instruction": ALLOWED_STRATEGIES[strategy_id],
            }
    return None


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def recoverable_runs(runs_directory: Path) -> list[str]:
    result: list[str] = []
    if not runs_directory.exists():
        return result
    for directory in sorted(runs_directory.iterdir()):
        if not directory.is_dir():
            continue
        try:
            state = read_run_state(directory)
        except ValueError:
            continue
        status = state.get("status")
        if status not in ACTIVE_STATUSES:
            continue
        owner_pid = int(state.get("owner_pid") or 0)
        if status in {"running", "verifying"} and pid_alive(owner_pid):
            continue
        if int(state.get("attempts") or 0) >= int(state.get("max_attempts") or 1):
            transition(directory, "failed")
            continue
        if status in {"running", "verifying"}:
            transition(directory, "interrupted")
        result.append(directory.name)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("run_directory", type=Path)
    init.add_argument("--risk", default="auto")
    init.add_argument("--max-attempts", type=int, default=2)

    show = subparsers.add_parser("show")
    show.add_argument("run_directory", type=Path)

    move = subparsers.add_parser("transition")
    move.add_argument("run_directory", type=Path)
    move.add_argument("status", choices=sorted(ACTIVE_STATUSES | TERMINAL_STATUSES))
    move.add_argument("--owner-pid", type=int)
    move.add_argument("--increment-attempt", action="store_true")
    move.add_argument("--strategy-id")
    move.add_argument("--action")
    move.add_argument("--complete-action")

    failure = subparsers.add_parser("failure")
    failure.add_argument("run_directory", type=Path)
    failure.add_argument("category")
    failure.add_argument("message")
    failure.add_argument("--action", required=True)
    failure.add_argument("--code", required=True)

    lookup = subparsers.add_parser("lookup")
    lookup.add_argument("root", type=Path)
    lookup.add_argument("fingerprint")
    lookup.add_argument("--action", required=True)
    lookup.add_argument("--scope", required=True)

    recoverable = subparsers.add_parser("recoverable")
    recoverable.add_argument("runs_directory", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init":
        result = initialize_run(args.run_directory, args.risk, args.max_attempts)
    elif args.command == "show":
        result = read_run_state(args.run_directory)
    elif args.command == "transition":
        result = transition(
            args.run_directory,
            args.status,
            owner_pid=args.owner_pid,
            increment_attempt=args.increment_attempt,
            strategy_id=args.strategy_id,
            action=args.action,
            complete_action=args.complete_action,
        )
    elif args.command == "failure":
        result = record_failure(
            args.run_directory,
            args.category,
            args.message,
            action=args.action,
            code=args.code,
        )
    elif args.command == "lookup":
        result = lookup_lesson(
            args.root,
            args.fingerprint,
            action=args.action,
            scope=args.scope,
        )
    elif args.command == "recoverable":
        result = recoverable_runs(args.runs_directory)
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
