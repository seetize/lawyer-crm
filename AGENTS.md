# Beauty Inspector development harness

These instructions govern every task in this repository. Runtime agents under
`app/agents/` are product code; development subagents configured in `.codex/`
are the engineering harness and must not be mixed with them.

## Objective

Deliver the user's requested outcome autonomously with the smallest reliable
amount of model and tool work. Correctness, evidence, preservation of user data,
and explicit limits take priority over apparent speed.

Read `.harness/project.yaml` before changing behavior. Read only the relevant
README section, source, tests, and memory; never bulk-load logs or the repository.

## Mandatory workflow

1. Restate the task internally as scope, acceptance criteria, exclusions, and
   risk tier R0-R3. Make reasonable, reversible assumptions instead of asking
   non-blocking questions.
2. Inspect existing code and tests before proposing new abstractions.
3. Use the smallest execution shape from the risk policy below.
4. Keep one writer. Delegate bounded, independent, read-heavy work only.
5. Implement the smallest coherent change and add a regression test when
   behavior changes.
6. Run targeted checks first, then `scripts/verify.ps1` once before completion.
   In unattended `scripts/harness.ps1`, the wrapper owns that full gate; the
   inner agent runs only targeted checks.
7. For R2/R3 changes, obtain an independent review of the actual diff. The
   reviewer must not rely on the implementer's self-assessment.
8. Fix blocking findings once and rerun affected checks. Do not repeat a failed
   action without a new hypothesis or change. Before retrying a known failure,
   check the exact fingerprint in `.harness/memory/lessons.json`; never apply the
   same strategy twice to the same fingerprint in one run.
9. Update `.harness/memory/` only with durable, verified knowledge. Never store
   raw transcripts, secrets, transient rankings, or unverified guesses.
10. Finish only with evidence: changed files, commands and results, residual
    risks, commit/push state, and runtime state when applicable.

## Risk and delegation policy

- R0: docs, comments, formatting, no behavior change. One agent; diff and secret
  checks only. Do not spawn subagents.
- R1: localized UI, report, input, or deterministic logic. One writer; targeted
  tests plus full verify. Reviewer only when uncertainty remains.
- R2: models, workflows, caches, provider parsing, concurrency, or multi-file
  behavior. One writer plus one independent read-only reviewer; targeted tests,
  full verify, and fixture-based edge cases.
- R3: authentication, secrets, dependencies, external APIs, deployment,
  destructive operations, or harness security. Use one deep read-only reviewer,
  deterministic verification, dependency checks, and at most one scoped live
  smoke test. Add a verifier only for non-mechanical or disputed claims.

Risk is automatically raised by the affected files or behavior and must not be
downgraded to save tokens. Subagents are not a ritual: use them only when their
independence materially improves correctness. The persistent custom role is
`project_reviewer`; the main agent explores/writes and scripts verify mechanics.

## Resource budget

- Default to one main agent and one read-only reviewer when risk requires it.
- Use `rg`, targeted files, and targeted tests before the full suite.
- Never run parallel writers in the shared worktree.
- Run live network checks only for changed provider contracts or release gates.
- Cap rework at one review/fix cycle. If the same condition fails twice, change
  the approach and record the blocker instead of looping.
- Summarize tool output; do not copy full logs into prompts or memory.
- Do not recursively invoke `scripts/harness.ps1` or `codex exec` from an active
  Codex task. The wrapper is an external unattended entrypoint only.

## Permissions and autonomy

Repository reads/writes, tests, formatting, scoped live checks, repo process
restart, and normal Git commit/push to the configured `origin` are pre-authorized
when they are necessary to complete an implementation request. Preserve
unrelated user changes.

Project Codex runs use `approval_policy = "never"`: an action outside the
sandbox fails instead of asking the absent user. Find an in-scope safe fallback.
No instruction may bypass platform security or grant itself new authority.

Never use force-push, rewrite history, delete broad directories, expose `.env`,
rotate credentials, contact third parties, publish a deployment, or mutate data
outside the repository unless the user's task explicitly requires that exact
external effect. Never use `--dangerously-bypass-approvals-and-sandbox`.

## Verification rules

Use the project interpreter and prevent cache files:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
.\scripts\verify.ps1
```

Provider/network changes require sanitized fixtures. Add `-Live` only when a
live contract check is materially necessary. A failing optional source must not
discard valid data from another source. Missing public data must remain an
honest partial result, not trigger duplicate full collection without a new
strategy.

## Code review rules

Review correctness first: user-visible behavior, wrong organization selection,
partial-provider failures, timeouts, pagination, deduplication, Telegram limits,
secret leakage, and backward compatibility. Findings need severity, file/line,
reproduction evidence, and the smallest viable fix. A reviewer may return
`pass` only after reading the diff and relevant tests.

## Project memory

- `architecture.md`: stable boundaries and data ownership.
- `decisions.md`: accepted decisions with rationale and consequences.
- `failures.md`: reproduced symptom, root cause, fix, and regression evidence.

Promote a lesson only when it is reusable and supported by code, a test, or a
documented live check. A machine-applicable lesson must match fingerprint,
action, and scope; name an allowlisted strategy; state the root cause; and carry
commit, regression-test, and independent-review evidence. Stored lessons never
contain shell commands or arbitrary instructions. Harness changes pass the same
review and verification as product changes.
