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
  2026-09-17 to shorten exactly this. Since 2026-09-19 the gap also **ends
  early, as soon as the miner delivers two hashing samples in a row** — the
  same evidence that releases a hold — so on a healthy boot it should end
  around a minute, not six. A full-length gap means the miner never proved it
  was back; that is a finding, not a timer.
- **Did the boot check fire?** `boot_check_minutes` after power returns it
  takes a reading, and a second one `boot_check_minutes` later before acting.
  On 2026-09-17 it did **not** fire although the draw was three to four watts.
  **Cause found and fixed 2026-09-18:** `observe()` cleared the pending check
  on any successful HTTP sample, and a cold start answers HTTP, so the check
  was always cancelled before it came due. It now clears only on a sample that
  is actually hashing. A cycle with no boot-check line at all is the same bug
  recurring: record it.
- **Did anything restart a healthy booting miner?** The risk introduced by the
  shorter settle gap. Measured boot time is 5-25 s of miner uptime, about a
  minute of wall clock, so a restart inside the first minutes is a regression.
  If you see one, say the settle cut needs revisiting.
- **Did the right remedy get chosen?** Reworked 2026-09-19 on the owner's
  principle: **a plug that measures watts is optional hardware, so the fault is
  decided on what the miner's own API shows and the meter only refines the
  remedy.** Reaching the boot check at all means the miner is not hashing. From
  there, "has it answered HTTP at all since power returned" is what separates
  the two cases, and it needs no sensor:

  | Answered HTTP | Wall draw | Meaning | Remedy |
  |---|---|---|---|
  | no | under `boot_watts`, **or no meter** | never powered up | cycle again |
  | no | at or above `boot_watts` | powered but hung | end the gap, soft restart |
  | yes | anything, or no meter | hashboard dead | end the gap, soft restart |

  Two consequences to check for. **A meterless plug now gets a working boot
  check** where it previously did nothing at all — if you see a cycle on a
  meterless plug with no boot-check line, that is a regression. And a box that
  answered HTTP is **no longer power-cycled a second time**; it is handed to the
  soft-restart ladder, which is what actually ended the 09-13..09-17 episodes.
  A second power cut on a box that was answering means the handover broke.

  The verdict always waits for a second reading `boot_check_minutes` later,
  with a "reading again in N min" line between the two. That is not caution for
  its own sake: every one of the five known cold-start failures drew **over**
  `boot_watts` transiently in its first 30 to 70 seconds before collapsing to
  2-10 W, so one reading can catch either the spike or the collapse. It is also
  why `boot_check_minutes` may not be set to 1.

  **The two timers can race, and which one wins is a config property.** The
  boot check reaches its verdict at `2 * boot_check_minutes` after power
  returns, while the restart ladder resumes the moment the settle gap ends. So
  with `settle_minutes` below `2 * boot_check_minutes` the ladder gets there
  first and the boot check never concludes. Seen on the bench 2026-09-19 at
  settle 2 / boot check 2: the ladder cycled at +3:21, the verdict was not due
  until +4:00. It is benign -- the ladder applies the same remedy and tries two
  soft restarts on the way -- and the defaults put the boot check first (6
  against 4). No validation rule enforces it, deliberately, because that would
  block the permitted `settle_minutes` of 2. Worth knowing before concluding
  the boot check is broken because a log shows no verdict line.
- **Do the counters agree?** `events.log` used to write "cycled #N today" with an
  N that disagreed with `/api/health`'s `cycles_today`. **Explained and closed
  2026-09-18:** the counter is a rolling 24-hour window and was always right;
  only the word "today" lied, because earlier cycles aged out of the window
  between the line being written and the reading being taken. The line now says
  "in 24 h". A genuine disagreement after this is a new defect, so still check.

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
mid-edit. Build in a worktree, then restart the service only if Python code
changed.

Merge with `--ff-only` **only when the branch was created after `633b948`**,
which added `.gitattributes` and renormalized line endings. A branch that
predates it holds CRLF blobs, so `--ff-only` fails and a plain merge shows the
whole tree as changed (559/559 lines of `api.py` on `gate2-pga`). Rebase those
instead, and check the result:

    git -c merge.renormalize=true rebase main <branch>
    # any append conflict in the EVOLUTION file: keep both entries, newest last
    git -c merge.renormalize=true rebase --continue
    git diff --numstat main      # must match the pre-rebase diff against the merge base Machine-specific notes live outside
this repo in the owner's working root, not here.

## Done-when discipline

Every task states Goal, Context, Constraints, and Done-when before work
starts. "Verified" means a command and its output, not a sentence.
