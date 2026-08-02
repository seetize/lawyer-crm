# Project harness

This directory is the versioned engineering memory and contract for autonomous
work on Beauty Inspector. It is intentionally plain Markdown/YAML: no database,
GUI, or additional runtime service is required.

## Flow

```text
user task -> risk/scope -> minimal execution -> deterministic checks
          -> independent review when needed -> evidence -> durable lesson
```

Interactive Codex tasks follow `AGENTS.md` and use native subagents. For a task
started outside an active chat, use:

```powershell
.\scripts\harness.ps1 -Task "Описание задачи" -Risk auto
```

The wrapper uses an approval-free workspace sandbox, stores only the structured
final result under `.harness/runs/` (not the raw request), and never disables
platform safeguards.

The workspace sandbox does not make `.env` unreadable to repository processes;
the no-secret rule is enforced by agent instructions and the pre-commit secret
gate. Live checks may use configured credentials but must never print them.

## Memory acceptance

Memory is not a transcript. Add a fact only when it is reusable, verified, free
of secrets, and likely to affect a future decision. Prefer a regression test over
a prose warning. Runs are ignored by Git; accepted lessons are committed in the
three memory files.
