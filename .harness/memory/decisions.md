# Decision log

## 2026-08-02 — Repository-scoped harness

Decision: use `AGENTS.md`, project Codex configuration, custom read-mostly roles,
deterministic verification, and Git-versioned Markdown memory.

Reason: a separate orchestration platform would add cost outside the parser's
scope. Native Codex subagents already support bounded delegation. One writer and
risk-based review reduce both token use and worktree conflicts.

Consequence: interactive tasks use native delegation; `scripts/harness.ps1` is
only an external unattended entrypoint. The system does not bypass platform
permissions and cannot grant itself authority outside the repository.

## 2026-08-02 — Bounded crash recovery and verified lessons

Decision: persist atomic run checkpoints, protect active task text with Windows
DPAPI CurrentUser, serialize writers with an OS mutex, and resume through a
limited-rights current-user watchdog. Use only allowlisted recovery strategies.

Reason: interrupted work must be resumable without exposing task text or turning
learned error data into arbitrary code execution. Repeated failure fingerprints
must change strategy and stop at a bounded retry limit.

Consequence: a crash preserves the worktree and can resume at user logon. A
lesson affects future execution only after root-cause, regression, verification,
review, and commit evidence. Unvalidated observations remain non-executable.
