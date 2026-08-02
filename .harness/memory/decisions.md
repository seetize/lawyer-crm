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
