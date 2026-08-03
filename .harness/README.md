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

The wrapper uses an approval-free workspace sandbox and never disables platform
safeguards. While a run is active, its request is stored with Windows DPAPI for
the current user; it is deleted after success. State is written atomically with
a backup, and the request reaches Codex over standard input rather than its
process command line.

`scripts/recover_harness.ps1` resumes interrupted runs. `scripts/watchdog.ps1`
checks the exact project bot runner and restarts it with a three-per-hour circuit
breaker. Install both separate current-user, limited-rights Scheduled Tasks with:

```powershell
.\scripts\install_watchdog.ps1
```

Bot checks stay short; recovery runs separately without a scheduler time limit.
Recovery is bounded to the configured attempts (two by default, three maximum).
A crash during the final gate resumes that gate without another model run. The
same failure and strategy pair is not repeated. A known workaround becomes
executable memory
only after root-cause evidence, a regression test, full verification, an
independent review, and a commit. This reduces recurrence; it cannot truthfully
guarantee that all future faults are impossible.

Validated entries in `memory/lessons.json` contain `lesson_id`, `fingerprint`,
`action`, `scope`, an allowlisted `strategy_id`, `root_cause`, and verification
evidence with `commit`, `tests`, and `review: independent-pass`. Text stored in a
lesson is never executed; the runtime substitutes its own versioned strategy.

The workspace sandbox does not make `.env` unreadable to repository processes;
the no-secret rule is enforced by agent instructions and the pre-commit secret
gate. Live checks may use configured credentials but must never print them.

## Memory acceptance

Memory is not a transcript. Add a fact only when it is reusable, verified, free
of secrets, and likely to affect a future decision. Prefer a regression test over
a prose warning. Runs are ignored by Git; accepted lessons are committed in the
memory files and `lessons.json`.
