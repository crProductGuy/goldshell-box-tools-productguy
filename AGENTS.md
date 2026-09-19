# AGENTS.md: goldshell-box-tools-productguy

Instructions for any coding agent working in this repository. Claude Code
reads this file through `CLAUDE.md`, which holds the single line
`@AGENTS.md`. Humans are welcome to read it too; nothing here is secret.

## What this project is

A dependency-free Python toolkit for Goldshell Box-series miners: a
dashboard that shows what the stock web UI hides, protected buttons for the
settings the stock UI cannot reach safely, a logger, a watchdog, a
clock-trials table, and an unattended clock-trial runner. `README.md` is the
front door; `docs/plan.md` is the design of record.

## Ground truth and the two logs

Read these before acting, in this order:

- `STATUS.md` — checkpoint: done, not done, open items, exact next action.
  Gitignored; exists only on the machine where the work happens. If it is
  missing, say so and work from `docs/plan.md` and the evolution log.
- `goldshell-box-tools-productguy-EVOLUTION.md` — the construction and evolution log,
  committed. One entry per session: the goal in the owner's words, questions
  and answers, proposals and decisions with reasoning, findings that changed
  the design, what was built and how it was verified, pushback, what was
  left out. Append an entry at every checkpoint. Never a credential, an
  address, a wallet, or another person's data; the file is public.
- `docs/plan.md` — decisions, package layout, order of work, deferred items.
- `docs/firmware-api.md` — what the firmware does; verified facts only.

## Standing check: read the aftermath of every power cycle

Added 2026-09-17 (session W). **If `events.log` shows a `power: cycled` line
since the last session, read what happened in the twenty minutes after it
before doing anything else, and report it.** Do not wait for the owner to
notice a bad day in the numbers.

What to look for, in `~/.gbox/log.csv` from the cycle timestamp forward:

- **Did the hashboard come up?** The known failure is a cold start where the
  controller boots and answers HTTP normally while the hashboard stays dead:
  `mhs_av` 0.0, both temps 0.0, fans spinning down, wall draw a few watts. It
  ends only when the stall watchdog restarts the unit. Five episodes between
  2026-09-13 and 2026-09-17; see the evolution log entry for session W.
- **How long was the dead stretch?** `settle_minutes` was cut 20 -> 6 on
  2026-09-17 to shorten exactly this. The open question is whether the stretch
  now ends near six minutes. If it does not, say so with the number.
- **Did the boot check fire?** `boot_check_minutes` after a cycle it should
  read the meter and, under `boot_watts`, cycle again at once and write a
  `power:` line saying so. On 2026-09-17 it did **not** fire although the draw
  was three to four watts. That defect is open and unproven as to cause, and
  `_check_boot` has no test coverage. A cycle with no boot-check line and a low
  draw is the same bug recurring: record it.
- **Did anything restart a healthy booting miner?** The risk introduced by the
  shorter settle gap. Measured boot time is 5-25 s of miner uptime, about a
  minute of wall clock, so a restart inside the first minutes is a regression.
  If you see one, say the settle cut needs revisiting.
- **Do the counters agree?** `events.log` has written "cycled #N today" with an
  N that disagrees with `/api/health`'s `cycles_today`. Open defect.

Telemetry never enters the main context raw: read it with a bounded subagent or
targeted `awk`, and report conclusions. The log is 6 MB and grows.

The bounded subagent is the `scanner` type (installed at user level on the
owner's machine from `agent-delegation-kit`; `browser-checker` for a page
check). **Never use the built-in `Explore` type or `general-purpose` without an
explicit `model`:** both run on the parent session's model, which is the most
expensive one. Write the brief from
`../agent-delegation-kit/templates/BRIEF-TEMPLATE.md`. To confirm which model a
scan ran on, count `"model"` values in its transcript under
`~/.claude/projects/<slug>/<session>/subagents/`.

## Rules that bound every change

- Standard library only. No new dependencies without a decision in
  `docs/plan.md`.
- One request in flight to the miner at a time, never more than one poll
  cycle per 10 seconds. The firmware's token check races and its web
  backend crashes under bursts.
- The service never proxies a settings write for the dashboard, and never
  gains a clock-changing endpoint. Clock changes come from the page (with
  the password) or from a CLI process.
- `log.csv` columns are only ever appended, never renamed or reordered; an
  older log is migrated in place on service start with a `.bak` copy.
- No secrets in the repo, no URLs in logs (the login URL carries the
  encrypted password), no request lines logged.
- Every non-trivial change ships with tests, and the tests are run. The
  fake miner in `tests/fake_miner.py` stands in for hardware; the real unit
  only with the owner pressing the buttons or explicitly approving a run.
- Bump the minor version when the log format, the CLI surface, or the HTTP
  API changes; tag every release `vX.Y.Z`.

## Working on the owner's machine

The live service runs from the main checkout and serves the web files from
disk on every request, so an edit to `gbox/web/*` changes the live dashboard
mid-edit. Build in a worktree, merge with `--ff-only`, then restart the
service only if Python code changed. Machine-specific notes live outside
this repo in the owner's working root, not here.

## Done-when discipline

Every task states Goal, Context, Constraints, and Done-when before work
starts. "Verified" means a command and its output, not a sentence.
