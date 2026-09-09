# AGENTS.md: goldshell-box-tools

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
- `goldshell-box-tools-EVOLUTION.md` — the construction and evolution log,
  committed. One entry per session: the goal in the owner's words, questions
  and answers, proposals and decisions with reasoning, findings that changed
  the design, what was built and how it was verified, pushback, what was
  left out. Append an entry at every checkpoint. Never a credential, an
  address, a wallet, or another person's data; the file is public.
- `docs/plan.md` — decisions, package layout, order of work, deferred items.
- `docs/firmware-api.md` — what the firmware does; verified facts only.

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
