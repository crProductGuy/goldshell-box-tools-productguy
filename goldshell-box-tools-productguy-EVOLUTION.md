# goldshell-box-tools-productguy: construction and evolution log

The record of how this project was defined, extended, and decided, session
by session: the owner's prompts (quoted or closely paraphrased), the
agent's questions, proposals and findings, and who decided what and why.
It is the source for `docs/how-this-project-evolved.md`, the essay, and it
is the file an agent reads with `STATUS.md` to pick the project up cold.
The rule for keeping it is in `AGENTS.md`.

The entries up to 2026-09-09 were reconstructed in one pass on 2026-09-09
from the local session transcripts, the git history, and the status docs,
after the fact. Entries after that date are written at the end of each
session. Nothing in this file is a credential, an address, or another
person's data.

Sessions covered by the reconstruction: A (09-05 morning to 09-06, the
diagnosis and the prototype), B (09-05 evening to 09-06 afternoon, plan
step 1), C (09-06 evening to 09-07, plan step 2), D (09-08 daytime, the
clock experiment by hand), E (09-08 evening to 09-09, clock trials, this
log, and the rule that requires it).

---

## Day 1, 2026-09-05: a tired miner and a diagnosis

**10:37, Mark.** "Investigate why the Goldshell SC BOX miner ... is running
a hashrate of only 30% of its nominal 900 GH/s." He pasted the stock UI's
numbers (avg 272 GH/s, fans 1200/1200, HW error 19 percent, 59.5 °C),
described the history (three good days, then a restart, then this), the
external fan he had added, and set the rules: the System page's Restart link
may be used no more than once in five minutes, "Think about this, give me a
plan for your research. Do nothing till I approve."

**10:39, Claude.** Read the machine notes, touched nothing, proposed a
working theory and a ranked list of causes: the fans at exactly 1200 are a
firmware floor, not a fan fault; the 19 percent HW error rate is the thing
to explain; candidates were power sag, failing chips or a voltage domain, a
hot spot the sensor cannot see, and a controller fault for the separate
"stops responding" symptom. Asked for the per-chip status data.

**11:17, Mark.** Supplied the web UI password and everything he had tried:
two PSUs at different voltages, two firmware versions, a pool change, the
temperature limit slider ("no effect on fan speed"), six to eight restarts
in three days. Noted the miner hashes faster with the external fan and
wondered whether it helps or hurts. "I approve phases 0 and 1. Ask me
anything, then go."

**11:25, Claude.** Findings from the miner's own logs and the hidden
per-chip data: the hashboard was in a re-initialization loop, "Read Nonce
Failed 10 Times, Reinit Device", resetting every nine seconds with ten
seconds of dead time each, 332 resets in the first hour, counted by a
`rebootcnt` the UI never shows. One chip, chip 8, produced 1460 of 1972
bad nonces while returning 34 good ones. Chips were running 75 to 94 °C,
not the 59 °C the UI reported, because the UI shows the board sensor.
Proposed a trial at 600 MHz through the manual power plan the stock UI
cannot reach, plus a logger.

**11:28, Mark.** "Go ahead with the 600 MHz trial ... I will be away about
an hour and a half." Set a token budget: "I don't want to spend more than
5000 tokens on that in total! Don't make a Fable-expensive polling loop!"
and the reason: keeping some hashrate going keeps his position on a pool
leaderboard.

**12:35, Claude.** One-hour result: board resets 332 to 0; hashrate from a
250 to 750 sawtooth to a steady 660 to 930; chip 8 from 34 good nonces per
hour to about 435; chip temperature from 75 to 94 °C down to 69 to 74.
Reading: chip 8 is marginal, not dead. At 725 MHz it fails timing and the
firmware resets the whole board; at 600 MHz it works.

**14:31, Mark.** "Leave it at 600, keep the watchdog running overnight."

**14:39, Mark.** "I don't see any of that data in the Miner page GUI! no
MHz, no strings, very basic." The gap between what the firmware knows and
what the stock UI shows became the project's reason to exist.

**14:44, Mark.** "bump up the fan speed to the highest you think safe ...
Leave me a one-line script to change clock and fan. Is there any way to
build a better web page with the data that's already there?"

**16:57, Mark.** "build the dashboard page, read-only status as you
recommend. We'll use it and probably add the controls later."

**17:33, Mark.** After seeing the first page: make it responsive (the
chart and the Miner box vanished at half a 24-inch monitor), add a time
scale with 5, 15 and 60 minute ticks, default fan 90 percent and show the
percentage.

**17:41, Claude.** Delivered all four plus a fan and temperature graph.
Found and reported that the fan field in the power plan is dead: the only
lever is the fan controller's target temperature on the board sensor.
Verified the history buffer's sample interval by timing it (2 samples in
112 seconds) before drawing the axis.

**17:50, Mark.** "good work on the responsive fixes! Looks OK even when
quite narrow!"

**17:56, Mark.** Pasted the browser console: the miner answering 401
sporadically. This became the "token check has a race" finding, the reason
every request in the toolkit is serialized and a 401 is retried before it is
believed.

**19:53, Mark.** "go ahead and try the tempcontrol flag experiment." (Ran
with sampling, an abort threshold and automatic restore; the flag does not
affect the fans.)

**20:24, Mark.** The turn that made it a product: "I'm going to want to put
a publishable version of this code into a public repo in my Crypto-focused
GitHub ... Other users of the SC BOX and maybe other Goldshell products can
find value in this. What license do you recommend for cypherpunks: MIT,
Apache, other? This is my gift to Bitcoin Mining with Goldshell Box series
machines. I also think I will want you to put (protected) buttons for
making changes, so people don't have to run scripts. I'm shooting for
near-normies who are somewhat technical ... Many will also be running this
on Linux ... Give me a tree of changes. We can build and test buttons first
before publishing."

**20:26, Claude.** The proposal: secrets and personal data out of the tree;
no hard-coded miner; the PowerShell/openssl encryption shell-out replaced by
about 120 lines of pure-Python AES-128-CBC so there are zero dependencies on
every OS; three processes become one `gbox serve`; model-agnostic display
with chip and board counts read from the miner; per-platform installers.
License: MIT, with the reasoning (Bitcoin Core's license, shortest, no
obligations on forks; GPLv3 rejected for a gift; Apache adds a patent grant
nothing here needs).

**20:26, Mark.** "I'd be OK giving users a pop-up to enter any needed creds
(as it does now) to avoid putting ANY secrets in a repo."

**20:26, Claude.** Agreed, then named the consequence: an unattended
watchdog with nothing stored sits idle after a reboot until someone types
the password again, "exactly when you want the watchdog awake" at 3 a.m.
Proposed the design that shipped: the dashboard is the only place a
password is typed; it hands the session token to the local service over
localhost, in memory only; the CLI prompts each time; `--remember` is an
explicit opt-in with the trade-off in the README; and the docs say outright
that this firmware's token is deterministic and never expires, so the
browser copy is password-equivalent, same as the stock UI.

**20:29, Mark.** "use your suggested repo name, and the page should work
standalone without the service." Two decisions in one line: the name
`goldshell-box-tools` and the standalone-page requirement that shaped the
credential flow and the CORS finding.

**20:42, Mark, session B.** Started the build from a brief with Goal,
Ground truth, Constraints and Done-when, ending "Restate the task before
acting." (This brief format, from the collaboration meta-prompt, opened
every build session after.)

**21:19, Mark.** On the first login modal: "unacceptably fiddly. You type
some stuff in the IP box and it starts emptying and overwriting. Typing the
password jumps up to the IP box again. FIx it." Six minutes later: "OK,
dashboard login worked and looks right on a quick look! Good rebuild!"

**21:26 to 21:28, Mark.** "Update the status doc and commit everything
green, then stop." Then "is the watchdog running for tonight, in case
anything bad happens?" Then "good, stop here for tonight." The checkpoint
prompt and the overnight question recur at the end of nearly every day.

## Day 2, 2026-09-06: the controller freezes, a security detour, and the push

**10:52, Mark.** "check now - mining pool thinks box is offline for a few
min."

**10:53, Claude.** The miner had been off the network since 10:12:34: no
HTTP, no ping, no ARP, while the rest of the LAN was fine. The watchdog had
correctly diagnosed "unreachable" and sent a soft restart three times; each
timed out because a frozen controller cannot take HTTP. Gave a three-step
checklist ending in a power cycle.

**11:12, Mark.** "power cycled it, check again. Was apparently hashing
before the power cycle, because it was pulling 190+ watts and throwing off
heat ... it's up now." (The manual 600 MHz plan survived the power cycle.
The hardware-watchdog gap, a smart plug on ping loss, went into the open
items and stayed there.)

**13:38, Mark.** "Run this curl command and save it in a security-notes
file about the Goldshell SC BOX family: ... disable WiFi. This should
prevent it from offering an open WiFi network of its own. Check that."

**13:40, Claude.** Could not scan for the network: since Windows 11 24H2 a
WLAN scan needs the Location privacy setting, a system setting it would not
change itself. Reported the evidence instead: the firmware object has no
access-point mode. Then raised something Mark had not asked about: the
miner held two WiFi networks and their password in plain text, readable
through the API, and recommended clearing them.

**13:52 to 14:09, Mark and Claude.** Four write shapes were accepted with
HTTP 200 and silently ignored while WiFi was disabled. Claude laid out two
options with a recommendation and a worst case. Mark: "Haha, those are not
my networks. Must have been un-erased from the previous owner of this
barn-find junky SC BOX." Then: "let's do the experiment. I want to know
this so I can share with others dealing with this flakey box product."
(Outcome: the firmware ignores the write in both states; `docs/security-notes.md`
records it. Claude also told Mark where the session transcript lives, who
can read it, and gave a one-line scrub for the credential that had passed
through it.)

**14:21, Mark.** "where are we on the plan to add this to my crProductGuy
github?"

**14:21, Claude.** "Not yet, by design," with each gate's status, then a
recommendation to wait for the buttons so the first public commit does what
the README promises, and the offer to push now if he preferred it off the
machine.

**14:24, Mark.** "I want to push now. Remind me the proposed repo name?"
Then: "1. created. 2. Protect email using the no-reply email. 3 is OK as
written. do it." (Squashed to one commit under the GitHub no-reply
identity, remote added, pushed; 0.1.0.)

**15:42, Mark, session A.** "Take STATUS.md out of the commit, then I'll
push the early release. I want to share it with someone who has HW issues
with a bad temp sensor making their BOX inoperable." Mark ran the push
himself from the prompt, hit the backslash-path error in Git Bash, fixed it
with forward slashes, pushed.

**21:40, Mark, session C.** The step 2 brief: four controls, confirm
showing the exact request, password re-entry for clock and restart, event
line on success, standalone and served.

**22:09, Claude.** Built and tested against the fake miner in a worktree,
"stops short of the real miner by design." Reported one deviation
explicitly: the revert-to-preset button also asks for the password, because
on this unit a preset means 725 MHz, the reset-loop clock. Sixty-six tests.

**22:11, Mark.** "1 and deploy, then restart the service and we will verify
together that the new dashboard is running."

**22:17, Mark.** Pasted the fan-target dialog's full request body: "shows
something scary: Info: powerplan 725." Two minutes later he pressed it:
"Done. The miner now reports plan 600 MHz ... fan target 66 °C ...
Observed: fans went up to full speed and loud." (The next morning's
follow-up made the dialog lead with a one-line diff per changed field and
fold the body under a toggle, and recorded the fan spike after any settings
write as expected behavior.)

**22:22 and 22:28, Mark.** "I'm going to bed now. Good work, see you in the
morning. watchdog is still running, right?" ... "save everything unsaved.
G'nite, mate."

## Day 3, 2026-09-07: what is safe to press, and the first step down

**11:08, Mark.** "check status this morning. Looks good to me from the
status page. Also think for a minute on what is safe for users to press on
the stock Miner page: are there SOME settings that are safe to 'Save', but
others not? Are we offering a replacement for those settings? Do we want to
make a 'replacement' Miner Settings page?"

**Claude.** Read the stock UI's page source rather than guess: the Miner
page's settings block has a fan-target slider and its Save handler always
writes `manual: false`, so nudging the slider silently returns the clock to
the factory preset. The rest of the stock UI writes to its own endpoints and
is safe. Proposed a per-page safety table for the README instead of a
replacement settings page, and an order of work. **11:19, Mark:** "I agree,
use your order."

**22:24, Mark.** "done at 575." Claude verified the change from the service
side: event line, plan, clock, zero resets, hashing through the change, the
fan spike. **22:26, Mark:** "I want to run it slower overnight tonight and
see if errors decrease on the bad chip 8. I set it to 500." This is the
first move of the clock experiment, and it was Mark's idea.

## Day 4, 2026-09-08: the experiment, by hand

**08:20, Claude, session D.** Caught up from STATUS.md, offered the
morning check; **Mark:** "yes, run it." (`scripts/chip-rates.py` was
written that morning: bad-nonce share of one chip per clock segment from the
log, with the rule "compare the bad share, not the count, because a slower
clock attempts fewer nonces.")

**09:30, Mark.** "I've changed the clock up to 550 MHz to test error rate
there. I want to optimize clean nonce rate = max output of valid shares for
mining." The goal changed here, from "stop the reset loop" to "find the
most productive clock." "We will watch for 2 hours, then change again ...
yes, set it up as a loop!"

**09:31, Claude.** Before scheduling: a cloud schedule cannot reach the
miner or this PC's log, so it would fire every two hours and fail; session-
only was the right fit. **Mark:** "session only for both reminder and
action."

**10:15, Claude.** First comparison table (600, 500, 550) and an anomaly
flagged rather than hidden: one 600 MHz segment computed at 1521 shares per
hour, 2.4 times the others, cause unknown, "treat that one as suspect."

**11:05, Claude.** At 575 MHz for 39 minutes: chip 8 showed its first bad
share since the clock came down, 1.69 percent, six nonces. "Could be
noise; worth letting it run longer." Asked whether Mark wanted to keep
watching or step back.

**13:20, Mark.** "Note cooler temps on sensor and chips around 13.00 EDT
after I put the external fan pulling on the outlet side of the miner box.
Box fan speeds dropped more than the temp. Do a quick error rate check."
(Board sensor down 2.4 °C, internal fans from 1860 to 1200 RPM and stayed
there. Airflow buys more fan speed than the fan target does.)

**Through the afternoon.** The two-hour check ran five times. Each report
was a table with the same columns; by 20:15, 575 MHz had held clean for
nearly ten hours at 0.10 percent bad share with the same six bad nonces.
"I don't have anything to flag; still your call on whether to keep running
the comparison at 575 MHz or step up toward 590 to 600 MHz."

**20:08, Mark.** "push now to GitHub to make the buttons public."

**20:13, Mark.** "tell me about these errors above: PreToolUse:Edit hook
error ... command not found." Claude traced it to a backslash path being
launched through Git Bash, which strips the backslashes; every edit for a
day had silently skipped its snapshot. **20:19, Mark:** "yes, fix the hook
slash issue."

## Day 4 evening to Day 5, 2026-09-08 to 09-09: turning the method into a feature

**20:49, Mark, session E.** With a screenshot of the previous session's
table: "I want to take the lessons from the logging in the last session I
just exited, and build error log counting and tabular display into the app.
Purpose: make it clear to others how to use the clock and fan speed
buttons, particularly clock slow-downs, to reduce errors from marginal
chips like we did ... Go into plan mode. I propose adding a table near the
bottom of the status page ... clock rate, duration, worst-chip bad share
percentage, bad shares/hour, board resets, HW error rate %, and accepted
shares/hour ... Give me a proposal for this, and draft user instructions for
how to run the controls and background data collection to allow the user to
systematically or even in a series of multi-hour trials, sequence through a
set of clock rates manually or automatically."

**Claude, plan mode.** Before writing the plan, ran a throwaway script over
the real log to ground the column definitions, and found two things that
changed the design. The 1521 per hour "outlier" had the same hashrate as
the 643 per hour segment: it was a different pool session, and pool
difficulty sets shares per hour, so accepted shares per hour is not a
throughput number and the table needed a hashrate column. And every counter
resets on a controller restart, so a run must break where uptime drops. The
plan added four log columns (all-chip nonce totals, fan target, overheat
flag) so HW error rate and fan setting come from the log rather than the
firmware's running average, an in-place migration of the existing log with
a backup, a segmenter, a dashboard table with a rollup per clock and fan
target and a toggle for individual runs, `gbox trials` for the terminal,
and `gbox trials run` for unattended stepping with abort rules. It listed
six decisions with the reasoning, the largest being that the runner is a
CLI process, not a service thread, so the service never gains a
clock-changing endpoint. It included a full draft of the user guide. Mark
approved the plan as written.

**Build.** Test-first throughout: 101 tests by the end. Verified in Chrome
against a scratch service running on a copy of the real log, which also
exercised the migration for real. A short trial against the fake miner ran
end to end. The security review of the branch found nothing. Merged,
service restarted 21:28, the real log migrated with a backup; the first
real sample confirmed that the firmware's Hardware Errors counter equals
the chips' bad-nonce sum.

**21:46, Mark.** "run the runner against the real miner."

**Claude.** Asked one question with a recommendation: which clock list, and
whether to run it in a visible console window (so Ctrl-C applies the end
clock) or in the background. **Mark** chose the recommendation: 600 MHz for
6 hours, end at 575, visible window.

**21:50, Mark.** Reported an error line from the window and a preference:
<!-- gstyle-ignore-start -->
"BTW I have been using PowerShell, not Windows PowerShell ... Note that you
should avoid powershell.exe and instead use pwsh.exe on this machine and
any other Windows machines of mine."
<!-- gstyle-ignore-end --> The error was Claude's own quoting
mistake in the launch command; the preference went into a memory and, at
Mark's request, into the machine notes file every agent reads.

**22:23.** The guard tripped as designed at the first check past 30
minutes: chip 8 at 8.5 percent bad share at 600 MHz, clock set back to 575,
exit code 2. Two days of the first observation reproduced in 34 minutes.
The first 29 minutes back at 575 showed 2.9 percent, which looked alarming;
by morning it was 0.11 percent over twelve hours. "Errors lag a clock
change" became a documented reading tip.

**2026-09-09, 10:55, Mark.** "Miner is warm but not hot, wall plug power
draw is a very steady 183W per a Kill a Watt in-line power meter (vs. 223W
when it was originally running at full stock speed) and very bouncy from
71 to 230 when the unit was resetting constantly. So 575 MHz is a nice
power-saver too, for those with expensive electricity ... This may be
useful to others." Then: "at 725 MHz stock frequency, I observed around
920 GH/s when I first ran the unit before it went unstable." The power
table in the guide followed, with the per-watt efficiency worked out (4.1
versus 4.0 GH/s per watt: efficiency barely moves, absolute spend drops 18
percent, and the miner hashes at all).

**16:19, Mark.** "Have you saved all the important learnings here into
docs?" Answer: mostly; four findings were only in the gitignored
STATUS.md. They went into `firmware-api.md` (a Counters section) and the
guide.

**17:07, Mark.** "review the plan, let me know if there are any
inconsistencies or things I might want to include." Seven inconsistencies
found, including one of Claude's own (a "v0.2" mention with the version
still 0.1.0), and six additions suggested. **17:15, Mark:** "bump to
0.2.0, tag both, and update the plan with the suggested additions," plus
the idea for this document and for documenting the stock UI's hidden debug
page, "because most users won't know about it, and you've gotten excellent
info there."

---

## Patterns worth drawing out in the narrative

- **Mark set the rules of engagement first, every time.** Restart at most
  once in five minutes; do nothing till approved; a token budget; the real
  unit only with Mark pressing the buttons; ask before acting. The
  collaboration got faster as those rules held, not slower.
- **The gap between what the hardware knows and what the vendor shows** was
  the product insight, and Mark saw it in one line: "I don't see any of that
  data in the Miner page GUI!"
- **Claude's most useful contributions were findings, not code:** the
  reset loop and the one chip behind it; the dead fan field; the token race;
  the Save-button trap read from the vendor's page source; the plaintext
  WiFi credentials nobody asked about; vardiff making shares per hour a bad
  metric; errors lagging a clock change.
- **Mark's most useful contributions were the questions that changed
  scope:** "is there any way to build a better web page"; "what license for
  cypherpunks"; "what is safe for users to press"; "I want to optimize clean
  nonce rate"; "build the lessons into the app"; the wall-power reading no
  log could see.
- **Disagreement was cheap and used.** Claude recommended waiting to push
  until the buttons existed; Mark pushed anyway to help a specific person,
  and was right to. Claude declined to change a Windows privacy setting and
  proposed the evidence instead. Mark pushed back on the fiddly login
  modal, the scary dialog body, the wrong PowerShell.
- **State lived in files, not in the session.** Every day ended with
  "update the status doc, commit everything green, then stop," and every
  build session started from a four-line brief. Five sessions, no lost
  work, one two-day trial reproduced in 34 minutes by the tool that trial
  produced.

## What stays out of this file

Session transcripts are private and stay with the owner. Credentials and
addresses that passed through them during the work are not in this file,
the repo, or the docs, and the log is read for that before every push.

---

## 2026-09-09, session E continued: the log becomes a rule

**Goal, in Mark's words.** "bump to 0.2.0, tag both, and update the plan
with the suggested additions" ... "document the debug page(s) in an
easy-to-find part of the repo, because most users won't know about it" ...
"I am also interested in creating a 'how this project evolved' ... Can you
reconstruct prompts and your questions and ideas?" Then: "OK to add the
long-form essay in the crProductGuy repo, written in neutral voice" and
"I'd like to require adding a 'project construction and evolution log' as
a formal tracking artifact to be created as we go, for anything I build
with Claude or other agents and harnesses."

**Questions asked and answered.** Where the log lives and what it is
called: at the project root as `<project-dir-name>-EVOLUTION.md` (Mark's
pick of the recommended option). Committed or local: committed, public by
default, with a rule against credentials and other people's data. Which
repos push to GitHub: this one only.

**Decisions and reasoning.** The runner stays a CLI process (unchanged).
Version 0.2.0 because the log format, CLI surface, and HTTP API all changed.
A per-project `AGENTS.md` for this repo, public, so the constraints
(one request in flight, append-only log columns, no settings proxy in the
service) survive being read outside the owner's working root.

**Built.** `v0.1.0` and `v0.2.0` tags; `docs/plan.md` brought up to date
(decisions table, layout, deferred items); `docs/stock-ui-debug-page.md`;
this log, reconstructed from five transcripts; `AGENTS.md` and `CLAUDE.md`
in the repo; the rule itself in the owner's root instructions and project
templates (outside this repo).

**Findings.** A "v0.2" mention had shipped with the version still 0.1.0
(the agent's own inconsistency, caught in the plan review). The
reconstruction surfaced that a credential typed into a prompt on day one
sat in a local transcript; it was redacted and a pattern-based scrub left
for the transcript of the session that quoted it.

**Pushback.** Claude asked whether linking the crypto GitHub identity to a
portfolio narrative was intended before writing the essay into the public
repo; Mark confirmed it was.

**Left out.** The essay is written after this entry; the meta-prompt repo
Mark proposed in the same turn is a separate project and gets its own
log.

## 2026-09-09, late evening: the rename, the second freeze, and a word banned

**Goal, in Mark's words.** "rename it to goldshell-box-tools-productguy,
do all of it." Earlier the same evening: "get rid of that damned AI-speak
'honest'. I NEVER say that, and neither should you, because it implies that
the speaker is shading the truth other times." And: "Keep the
preset-picker password."

**Decisions and reasoning.** The name: Mark first proposed a `pg-` prefix
for "Product Guy"; Claude recommended against it because `pg` reads as
PostgreSQL to most developers and offered `productguy-` as prefix or
`-productguy` as suffix; Mark chose the suffix. The package name and the
`gbox` command stay as they were. "Honest" and its relatives ("honestly",
"candidly", "frankly", "unvarnished", and kin) are now a rule in Mark's private
anti-patterns file and a failing check in the public agent-style-guide;
five occurrences were removed from this repo's docs, two of them
pre-dating this session. The preset picker keeps its password prompt:
every clock change asks, no exception for the most dangerous one.

**Built.** The rename on GitHub (Mark, in the browser, as the repo owner);
remote URL, README, AGENTS.md, plan, essay, homepage URL, and this file's
name updated. The local directory rename is deferred to the next session,
because this session's shells held the directory open and Windows refused
the rename; the Startup launcher was briefly pointed at the new path, left
the service down for a few minutes, and was reverted.

**Finding.** During that few minutes the log showed the miner had been off
the network since 19:00, the second frozen-controller episode after
2026-09-06: six watchdog restarts timed out, the daily cap was hit at
20:29, no ping, no ARP. Mark power-cycled it at about 21:50. His wall
meter read 53 W while it was hung, against 183 W hashing, with barely warm
air: the hashboard idle, only the controller alive. That number is now in
`firmware-api.md`, and the plan's deferred-items table records that the
trigger for a hardware watchdog has fired.

**Pushback.** On the prefix, above. And when Mark asked whether Claude
could switch the browser's GitHub account for him: no, because that is his
sign-in; he did it himself.

## 2026-09-09 night to 09-10, session F: the power-cycle module, researched and proposed

**Goal, in Mark's words.** "go into research mode, then planning mode
overnight. I want to add an optional module to this tool to allow
auto-power-cycle of a hung miner. I have TPlink Kasa brand smart switches
around here, including an HS-105 ... already inline with this miner power
plug ... 1) Estimate the market share % of the top 5 wifi-controllable
consumer smart plugs ... 2) check for any other repos that I overlooked.
3) quick action: scan that HS-105 ... It has an energy sensing readout in
it ... 4) go into planning mode, devise a plan for adding this feature,
with effort estimate including making it windows, Mac, and Linux-friendly
... Give me a secondary recommendation for a smart plug brand or set of
models to support, including home PDU modules." The trigger was the
second controller freeze, the evening before: a soft restart cannot reach
a frozen controller, and only a power cycle clears it.

**How the session ran.** Unattended, so every question was written into
the proposal with the assumed answer instead of asked. Three research
agents ran in parallel with search budgets and a stop rule; the plug
probe ran in the main session, read-only by decision, since the miner was
believed to hang off that plug. The test suite was run once at the start.

**Findings that changed the design.** The plug named as inline with the
miner reported its relay off while the miner hashed, so it was not the
miner's plug; LAN discovery found a second Kasa plug with an energy meter
reading a steady 188 W, matching the wall meter. The HS-105 has no energy
meter on any hardware version, contrary to the brief. Two consequences
went straight into the design: an `init` command that records the plug's
device id and refuses to cycle any other device, and a `discover` command,
because the owner's own recollection of which plug was where was wrong.
Four minutes into the trace the miner froze for the third time, 34
minutes after the previous power cycle, and the metering plug's reading
fell from 188 W to 38 W in the same minute: the identity question was
answered by the failure the module is meant to fix. The session did not
cycle the plug (not authorized, and the push notification to Mark could
not be delivered); the plan records the open decision. The plan came back
approved with no word from Mark, read as the harness's auto mode rather
than Mark, so nothing was built until he spoke.

**23:27, Mark.** "recheck miner, I think it's hashing now, 183W ... I
don't think it needs a power cycle. confirm." Confirmed: the miner had
come back at 23:13 with the plug's relay closed the whole time, the first
self-recovery in three freezes. **23:30, Mark:** "I didn't touch the
power or the machine, so maybe it did self-recover. I like the 15 min
power cycle if apparently dead. What do you need from me to build to the
plan?" Three questions, three answers: build tonight unattended in a
worktree and stop before merge; commit the proposal with the plug names
removed; one security pass at feature-complete.

**Built, 23:35 to 00:05, test-first throughout.** Phase 1: the driver,
discovery, the fake plug, the config block, the four `gbox power`
commands. Phase 2: the watchdog rung, the watts column, the health block,
the page's service line and marker, the wiring in `gbox serve`, the
guide, README, plan, security and firmware notes, version 0.3.0. From 101
tests to 165 plus 21 in JavaScript. Two mistakes caught by the tests and
worth recording: a config block assigned directly skipped the defaults
merge (fixed by making it a property), and a test that used the default
service port reached the live service and wrote one stray line into the
real event log (fixed by pointing test configs at a dead port). The
security pass found nothing above threshold and suggested cleaning plug
names before they reach a log or a terminal, since anyone on the LAN can
rename a legacy Kasa plug; built and tested. A scratch walk-through with
the fake miner and fake plug showed the dry run writing "would cycle"
after the second failed soft restart and, armed, cycling the fake relay
and counting one cycle on the page. Nothing merged, nothing restarted,
nothing pushed: the branch waits for Mark.

**Decision taken without Mark, flagged for him.** A second cycle after a
settle gap needs the whole ladder again (two fresh failed soft restarts),
the more conservative reading of the plan, rather than firing at the
first judgment after the gap. **Morning of 2026-09-10, Mark:** "I agree
with your call." He also had the stray test line deleted from the live
event log. The night was clean: 12.7 hours at 575 MHz, 19 bad nonces of
128,705, chip 8 off the weak list.

**Noon, Mark.** Asked what comes next and whether to provoke a real
plug cycle, run up the clock, or build more. Recommended: merge and
deploy; one deliberate cycle; a watchdog-provoked cycle by pointing a
second service instance at a dead address (the only on-demand test of
the judgment path against real hardware, one power cycle); no clock
increase, since nothing ties the freezes to clock and a higher clock
only reproduces chip 8's known failure; then watts per clock in the
trials table, the Linux installer, the Shelly and Tasmota drivers, the
case study, KLAP last. Mark: "Do 1-4 now ... I authorize you to
power-cycle the Kasa when you get to that. I accept your recommendation
on not running up the clock." Asked whether to compact or restart the
session first; the answer was restart, per the session-hygiene rules
(14 hours, several gates), with the brief written into `STATUS.md`. The
session ended there; the merge and the cycles belong to the next one.

**Research.** No public source gives brand-level smart plug share;
estimates were given as ranges with the basis stated, TP-Link plausibly
first. Home Assistant's install counts put Shelly nearly two to one ahead
of Kasa among local-control users, which set Shelly as the second driver.
Fourteen repos were inventoried; none is usable as a dependency (standard
library rule, and the reference one is GPL), and none is needed: the
legacy protocol is about sixty lines. The newer KLAP protocol needs an
AES decrypt the repo lacks and account credentials, and no device on this
LAN speaks it.

**Proposed.** The cycle as the next rung of the existing watchdog ladder:
unreachable, two soft restarts failed, fifteen minutes, plug answers with
the right identity and reports on, under the daily cap. Dry run by
default, so the log proves the judgment before the relay moves. No
dashboard button, keeping the service free of state-changing endpoints.
Watts as an appended log column, evidence rather than a gate. Effort about
one and a half sessions for the feature, half more for Shelly and
Tasmota, one for KLAP that cannot be verified here.

**Left out.** Any write to either plug; SNMP, Matter, Meross and Wemo;
the directory rename from the previous checkpoint, which needs a session
started outside the directory.

## 2026-09-10 midday, session G: 0.3.0 deployed, the plug proven twice

**Goal, in Mark's words** (from the brief he approved at noon, carried
in `STATUS.md`): "deploy the power-cycle module and prove it on the real
plug, in four steps": merge and release 0.3.0, record the plug, one
deliberate cycle with the miner hashing, one cycle provoked through the
watchdog, then leave the live service armed. "I authorize you to
power-cycle the Kasa when you get to that. I accept your recommendation
on not running up the clock." The session was told to restate the task
before acting, and did: goal, context, constraints, done-when.

**Deploy.** The branch's 165 tests were run once more before the merge
and passed. `main` fast-forwarded, the previous session's log entry
committed, `v0.3.0` tagged and pushed. The service restarted from the
Startup launcher and reported 0.3.0; it widened the log to 22 columns.
One small mismatch surfaced there: the event line says a copy was kept
as `log.csv.bak`, but the migration deliberately never overwrites an
older backup, so the `.bak` on disk is still the 21-column copy from
09-08. Design, not fault; the wording is looser than the code.

**Recording the plug.** Discovery listed the same two plugs as the night
before: the HS105 with its relay off and the HS110 reading 187 W with the
miner hashing at about 183 W on the wall meter. `gbox power init` refused
a piped "y" ("no terminal to confirm on: pass --yes") and was rerun with
`--yes`; the guard did what it was built for and the flag is the
intended unattended path. Dry run first, one more restart, status
showed the plug and 187 W in the new column.

**The deliberate cycle.** `gbox power cycle` needs a terminal and the
typed word CYCLE, and Git Bash's winpty refuses piped input, so the
agent could not confirm it programmatically. Rather than work around the
guard, it opened a visible PowerShell 7 window at the prompt and waited;
Mark saw the window ("I saw this modal over this terminal") and typed
the word. 12:11:07: relay open 15 s, then closed. The meter went 187 W,
39 W, 38 W, 190 W on the 30 s polls; the miner answered a timeout, then
an HTTP 500 while booting, then a good sample 66 seconds after the cut,
at 575 MHz manual with zero board resets. Sixty-six seconds is faster
than the two to three minutes the guide promises, and under the
watchdog's two-minute unreachable threshold, so the live watchdog never
stirred. The live config was then armed and the service restarted;
status reads ARMED.

**The provoked cycle.** The only on-demand test of the judgment path
against real hardware: a second instance on a scratch data directory,
its config a copy of the live one with the miner address changed to a
LAN address that answers neither ping nor ARP, port 8767, power armed.
`--remember` from the brief was dropped as unnecessary, since the copied
config already carries the stored password. A Monitor watched its event
log and a detached 40-minute stop-loss stood ready to kill it, because
after the settle gap the ladder would run again and cycle a second time.
The ladder ran as designed and on the
clock the guide predicts: login timeouts from the first poll, the first
failed soft restart at 12:18:27, the second at 12:32:58 after the
ten-minute gap, and at 12:33:13, with the episode 17 minutes old, `power:
cycled #1 today: off 15 s, on (miner unreachable for 2 min; 187 W
before)`. The scratch instance was killed 30 seconds later, then the
stop-loss timer. The live service, which had no part in the decision,
recorded the outage from the other side: a timed-out poll with the plug
at 39 W, an HTTP 500 while the controller booted, and a good sample at
12:34:12, 60 seconds after the cut, at 575 MHz with zero board resets and
189 W. Its own watchdog never reached the two-minute threshold, so the
live event log carries only the deliberate cycle; the provoked one lives
in the scratch instance's event log, copied into the data directory as
`provoke-2026-09-10/`. Two power cycles, both authorized, both survived
at the manual clock.

**Decisions taken without Mark, flagged.** Using `--yes` on `init` after
the piped answer was refused (the identity condition in the brief held:
the plug whose meter fell to 34 W during the freeze). Opening a window
for the CYCLE word instead of bypassing the guard. Omitting `--remember`.

**Left for later.** The worktree removal, the directory rename and the
transcript scrub still need a session started outside the directory. The
event-line wording about the `.bak` copy. The trials table's watts
column and GH/s per watt are next in the accepted build order.

## 2026-09-09 23:18 to 2026-09-10 16:40, session H: a screenshot read, a wrong guess corrected, the freeze pinned down

**The goal, Mark, 23:18.** "check the miner. Report looks funny. Fans full
but doesn't seem to be hashing. maybe needs a soft reset?" Then a PDF of
the dashboard at 23:15, two minutes after the third freeze ended.

**Read from the service, not the miner.** The log showed accepted shares
climbing, 700 GH/s, fans already falling from 4440 to 3840 RPM: a fresh
boot, not a fault. Answer: no soft restart; it would only wipe the
firmware's history buffer again and send the fans back to full.

**A wrong guess, corrected.** The agent said "something power-cycled it
at 23:13", inferred from the 42 s uptime. Session F, running at the same
time, had the plug's meter and knew the relay never opened: the first
self-recovery in three freezes. Restated to Mark the next day.

**The screenshot explained.** Empty hashrate graph: the miner's 288-slot
buffer is wiped by a boot and had one sample, and a one-point path draws
nothing (open item). Fans at 90 percent: how the fan daemon starts. Chip
8 "failing" at 4 bad of 69: noise in the first minutes, under 1 percent
within the hour every time.

**Mark, 16:30:** "have you saved this history ... and the service log
perf table?" No: the raw rows were in the service's files, the read-out
was only in the session. Pinned the window (last good row 22:21:53 with
nothing abnormal in it, unreachable 51 min), pasted `gbox trials` verbatim
into STATUS.md, and at Mark's word copied STATUS.md beside his
miner-status PDFs in OneDrive. Nothing on the miner or the service touched.

## 2026-09-10 16:19 to 17:40, session I: the scrub, the rename, and what was holding the door

**The goal, Mark, 16:19.** "pick up goldshell project, read status.md, do
the directory rename (goldshell... to goldshell...-productguy, I believe -
check the notes). scrub the transcript. What are next steps after that?"
Two chores that three sessions had deferred, each because it had started
inside the directory it was meant to rename.

**The scrub ran first** and found the password ten more times in the
2026-09-08/09 transcript, in three encodings; all replaced. The script's
directory list was widened so a later run reaches transcripts of sessions
started from the parent folder or from the renamed checkout.

**The rename failed twice more**, "being used by another process", with
the service stopped and this session's shells parked outside the
directory. Rather than kill by guesswork, the agent wrote a short
PowerShell script that reads every process's working directory from its
process block, since no Sysinternals tooling is installed here. It named
the holders: two idle Claude Code sessions from earlier days, the console
the 09-08 clock trial had run in, three orphaned `tail -F` monitors left
behind by earlier Monitor calls, two Explorer windows, and two leftovers
of session G's own tests. The lesson, now in the machine notes: Windows
will not rename a directory while any process has it as its working
directory, and idle agent sessions and file-manager windows count.

**Pushback from the harness, and a question.** The auto-mode classifier
refused to end the two Claude sessions, and earlier the worktree removal.
The agent stopped and asked; Mark closed every stale session and window
himself. The Explorer windows were navigated away instead of closed. While
the question stood, the service was relaunched from the old path so the
miner was not left unwatched.

**Then the routine part:** rename, launcher path, relaunch, health check,
`git worktree repair` for the merged worktree the classifier would not
let go, memory copied to the new project path, notes updated, export
re-run, 165 tests green from the new location.

**Mark, 19:16: "check out the 6-minute visible gap in the fan speed and
2 temp traces on the graphs, from 16:22 - 16.28."** With a screenshot.
The log settled it: the gap is the service stopped for the rename attempt
(last row 16:23:46, `service: started` at 16:28:55, every row on either
side `ok`), and a second, shorter one at 17:27 to 17:29 for the rename
itself. The hashrate chart above has no hole because it comes from the
miner's own buffer. The agent noted that the chart cannot tell a service
outage from a miner outage and offered a marker at each service start;
Mark: "yes, please. add that!"

**Built (0.3.0, web files only, no restart):** `service: started` lines
now join the marker set on the fan chart, drawn as S, hover for the line;
other `service:` lines still do not. The caption gained a one-line legend
for the four glyphs. Test-first: the marker test was rewritten to expect
the S and to keep excluding the token line, failed, then passed; 21 JS
and 165 Python tests green. Checked in Chrome against the fake miner on a
scratch service: a dashed line at the start time with the right hover
text. Two things learned on the way: the browser had cached last night's
`app.js` for that port, so a hard reload was needed, and the page only
redraws markers on its 60-second service tick, so a check inside the
first minute shows nothing. Merged fast-forward; the live dashboard
serves the new file at once.

**Mark, 21:5x: "Push what you can. I don't understand what Housekeeping
Item 1 is."** Pushed. The item was the merged power-cycle worktree still
sitting in the project folder; explained as a leftover copy of the code
whose branch was already in `main`, and removed at his word. Then: "do
the two wording fixes and the one-point chart."

**Three small fixes, test-first, one worktree (`902e9cc`).** The
migration event line had claimed "copy kept as log.csv.bak" every time,
while the code deliberately never overwrites an older backup; now
`migrate_columns` returns whichever note is true ("copy kept" or "the
older log.csv.bak was left as is") and the service writes that. The
cycle command had promised two to three minutes to boot; the SC-BOX was
measured at 60 to 66 seconds twice, so it now says about a minute and
allows two or three on other units, with the guide's example matching.
The hashrate chart, handed a buffer with one sample after a boot, drew
an axis and nothing else; the buffer handling moved into a small tested
helper and one sample now yields a sentence saying what the number is
and that the graph starts at the next sample. Each test was written to
fail first, and did. 22 JS and 166 Python tests green. The one-sample
note was seen in Chrome by pointing the fake miner at a fixture copy
whose buffer holds a single value. No service restart: nothing running
in the service changed behavior before its next start.

**Left for later.** The glyph hiding under the series label when the
event is recent, and the accepted build order, unchanged: watts and GH/s
per watt in the trials table next.

## 2026-09-10 late to 2026-09-12 11:45, session J: 0.4.0 planned, expanded, built and released; the power rung proves itself overnight

**The goal, Mark, 2026-09-10 late evening.** "Go into planning mode and
make a proposal 1) adding Watts and GH/s per watt in the Trials table, 2)
for adding a graph line for watts along the same timescale ... in a new
graph section below Fan and Temp, with a note that it's from supported
TPLink Kasa devices ... 3) a box for current wattage at the top, after
Shares and Clock." Then, the next day: "Add to the rolling log at the
bottom: interventions that this software made ... Add right-hand '% of
Max' axes on the graphs where the max can be known ... In the future, I
want to generalize this to Goldshell SC Lite models ... Advise and ask me
if there are any reasons to split this work."

**Questions and answers.** Rows logged before the plug show "?" (no
manual Kill A Watt table). The Power tile is always shown. 0.4.0 with a
tag, because two endpoints gain fields. The finder he had in mind was
find.goldshell.com, which had failed to show his own running unit. No
other Goldshell hardware here or coming; owners of other models test the
drafts. Fan percent is RPM over the model's maximum RPM, not the
firmware's duty cycle, because reading the duty cycle every poll would
add a few-hundred-KB fetch to each cycle. The interventions table
includes his own actions. Split as advised: 0.4.0 now, other models as
0.5.0 with its own plan.

**Research that shaped the plan.** The other developer's repository for
the SC Lite uses the same web API family (same login handshake, same
settings endpoint), which means the login and settings code carries over
unchanged; what differs is per-board data from a cgminer endpoint that
returns 500 on the BOX, a debug page that can be locked, a power-plan
string with a millivolt and a trailing term, and a fixed 85 °C fan
target. Goldshell's finder is an account-based listing, not a LAN scan.
The SC-BOX and SC-BOX II spec pages would not render to a fetch; their
figures come from retailer listings that agree, and the model table says
so per row. The plan file records all of it with sources.

**Built, test-first, one worktree per step.** A regression from the
previous session came first: the hashrate hover read a variable the
one-sample fix had removed; fixed alone, merged, live. Then the trials
columns (a segment's watts is the mean over the rows that have a reading,
with its own count, because the plug can miss a poll; a rollup weights by
that count; GH/s per watt always in GH/s), a model table in both
languages with a test that keeps them identical, the health endpoint
gaining the miner's model, its rated figures and the plug's name, the
page's log-row parser made null-safe for watts (a blank cell is a hole,
never zero), the interventions parser with its join to the samples for
"miner back after N s", the shared panel scaffold with a right-hand
percent axis, the watts section with the verified Kasa list and
TP-Link's link, and the Power tile. 175 Python and 26 JS tests. Verified
in Chrome against the fake miner and fake plug; one screenshot showed
the axis title clipping, fixed. The security pass found nothing; its one
note, a non-string model value crashing the health handler, was fixed
with a test. Tagged v0.4.0, pushed, one service restart.

**What the log had been doing while the plan was written.** The
acceptance test the previous checkpoint named ran three times overnight:
freezes at 23:43, 05:40 and 06:25, each followed by two failed soft
restarts and a power cycle, each cycle bringing the miner back in about
a minute. A fourth freeze at 10:25 met the daily cap of three cycles and
the miner stayed hung. Four freezes in eleven hours against three in the
week before: a change in the unit, worth watching. **A gap found on the
restart:** the caps are in-memory counters, so the 0.4.0 restart handed
the watchdog three fresh cycles on a still-hung miner. Within what Mark
armed, and the ladder will most likely recover the unit, but a restart
should not reset a safety cap; reading the counts back from the event
log at start is the fix, recorded as the next item.

**What happened next (watched live).** Soft restarts timed out at
11:31:42 and 11:46:14, the plug cycled at 11:46:29, and the first good
sample came at 11:47:30: 575 MHz, 189 W, 61 seconds after the cut. Four
automatic recoveries in twelve hours, every one within about a minute.
Mark: "write all this to notes if not already." Done: STATUS.md carries
the timeline, the cap gap with its fix and a brief, and the watch on the
unit's freeze rate as his call.

**Left for later.** The cap persistence, first. The 0.5.0 plan for other
models. Event-log rotation. The glyph under the series label.

## 2026-09-12 afternoon, session K: a PSU swap logged, the caps made to survive a restart, the clock under "now", the version in the header

**Mark's brief.** First: "pick up goldshell project, read status.md,
stop." Then, by hand, a manual change to log: the 360 W fanless power
brick (about 11.8 V DC out) replaced by a Bitmain APW3++ 1600 W, on at
14:45, 12.18 V DC to the miner under load at 575 MHz, the in-line meter
reading 180 W, 184 VA, 0.98 PF, 1.5 A at 119.2 V. Then: "let's roll on
next planned SW improvements," plus two page asks: the time under "now"
in the hashrate graph ("it's kind of hard to notice the most recent
update time in the upper right") and the software version somewhere easy
to see, the header line being fine. Then the caps: "Make sure that the
daily cap is high enough to accommodate these controller-going-AWOL
events that are now more frequent. Uptime of hashrate is more important
to me than capping interventions. I'd be OK with 8 power-control restarts
a day if needed. Keep smacking it so it runs."

**The swap, and a flag withdrawn.** The event was posted through the
service's own event endpoint so it lands in the interventions table as
"you"; the 200-character cap on a posted line cut the first attempt in
the middle of the meter readings, so those went in a second line (twice
this session; a warning or continuation lines is a small item for
later). The plug's meter read 55 W while the miner hashed, which the
agent flagged as the miner no longer being on the HS110. Mark: "I think
the measurement you flagged was transient as the system was powering up."
The log said otherwise on both counts: not a plug mismatch (197 W steady
from 14:56:39, matching the Kill-a-Watt and Mark's app), and not the
power-on either. Two hashboard resets at 14:55:39 and 14:56:39, ten
minutes after power-on, with chips back to 31 C and the firmware
reporting 50 MHz for one sample. Mark: "I wasn't touching anything at
14:55, those resets count." They are the first resets at 575 MHz since
the clock trials ended; the brick ran zero for days. The draw at 575 MHz
was 181 to 183 W before them and 196 to 198 W after, a step, not a ramp;
the brick had given 187 to 193 W the same morning. Core voltage is not
visible through the firmware API, so the cause is a guess, recorded as
one. Observation period: about a day, reset count against the brick's
zero, at about 10 W more at the wall.

**Design, bounded, approved in one round.** Three items in one 0.4.1:
the caps read back from the event log at start (the gap found in session
J), the wall-clock time under "now" on every chart (Mark: "all charts"),
and the page's own version in the header after firmware and uptime.
Mark declined a security pass ("no security pass"); the agent had
already said the only new input is the service's own event log.

**A finding that changed the caps.** Reading the ladder before raising
the cycle cap: a cycle needs two failed soft restarts, and `check()`
takes a restart slot before the PUT goes out, so every cycle costs two
of the watchdog's own `max_restarts_per_day`. With the live values (6
restarts, 3 cycles) the restart cap would have stopped the ladder one
rung short of an 8-cycle day without a word in the log. So: the config
now refuses a restart cap under twice the cycle cap, the default restart
cap goes from 6 to 12 so the defaults do not block their own third
cycle, and Mark's config went to 20 restarts and 8 cycles.

**Built and verified.** Test-first: six watchdog tests (recent lines
only, failed attempts count as they do live, hand cycles never, garbage
ignored, the newest seeded restart or cycle holds its settle gap, and
the done-when: three seeded cycles refuse a fourth and log the cap
line), three config tests, two JS tests, one Python test that keeps the
page's `VERSION` equal to `gbox.__version__`. 185 Python tests green (the
JS suite runs inside them). In Chrome on a scratch service against the
fake miner with a hand-written events log: the service wrote "watchdog
picked up 1 restart and 3 cycles from the last 24 h of the event log;
the daily caps carry on", health showed `cycles_today` 3, the header
read "fw 2.2.5 · up 10 h 27 min · gbox 0.4.1", and "16:58" then "16:59"
sat under "now" on the hashrate and the fan and temperature charts.
Merged fast-forward, tagged `v0.4.1`, the live service restarted at
17:00:43: it picked up 9 restarts and 4 cycles, the same numbers a grep
of the last 24 hours of the log gives. Live headroom at that moment: 4
cycles and 11 restarts before the caps, and the overnight lines start
rolling off the window at 23:45.

**Left for later.** Not pushed (Mark pushes). The ladder's timing was
not touched: with `after_minutes` 15 a freeze costs about 17 minutes of
hashing before the plug moves, and Mark's "uptime over interventions"
argues for shortening it; recommended, not done, since he asked for
caps. The event-line cap warning. Then the 0.5.0 plan, the Linux
installer, the other plug drivers.

**Addendum, 17:05 to 17:30: 0.4.2.** Mark: "0.4.1 is very good," then
four asks. The service log reversed, newest first, "like Interventions
... easier to see relevant things in a limited window." The ladder
defaults cut ("I like your recommendation ... like 5 minutes") with the
values shown on the page "to make it obvious how to configure them":
`min_gap_minutes` 10 to 5 and `after_minutes` 15 to 5, so the plug moves
about 7 minutes into a freeze instead of 17; the Service section now
carries a "Ladder:" sentence built from a new `ladder` block in the
health endpoint, ending with the config file's path and "restart the
service after editing." The posted-event cap raised ("anything up to
500 chars"): 500, the top of his range, since the cap only bounds abuse
and the page is loopback-only by default. And a proposal, not code, for
the tiles whose "since start" and "since page opened" leave a viewer
unable to tell what the numbers mean; given in chat for his pick.
Test-first again: two server tests, two JS tests, the config test
re-pinned. 187 tests green. Verified in Chrome on the scratch service.
Merged, tagged `v0.4.2`, live at 17:24:29 with the same seed line (9
restarts, 4 cycles). One judgment call flagged in STATUS: additive
health fields shipped as a patch version, not a minor.

**Addendum, 17:30 to 18:10: 0.4.3, the tiles; the checker fixed upstream.**
Mark picked two hashrate tiles over one and said build it. The rule that
came out of the proposal: every number names its window with a clock
time the viewer can see. The header shows "up 10 h 27 min since 07:33";
the average tile is "Hashrate since boot 07:33" with the last hour's
mean from the miner's own buffer under it (so it works as a file too);
HW error rate and board resets read "since boot 07:33 · N in the last
hour" from the service log, the hour being sums of row-to-row increments
so a boot's counter reset never shows as a negative; opened as a file
with no log they say "since 18:00 (page opened)" instead of the old
"since page opened" with no time. Three JS tests, one existing row-shape
test extended. 187 Python and 33 JS tests green; Chrome on the scratch
service. Merged, tagged `v0.4.3`, live at 18:01.

The three style-checker failures in this log were false positives, and
Mark asked for the checker fixed rather than the text: `is_quoted` only
looked at the match's own line, so a quotation wrapped across lines (a
log quoting what someone typed) flagged its words. The fix in
agent-style-guide (`a31792b`, pushed) also checks the enclosing
paragraph for a quotation spanning lines, capped at 800 characters; two
tests. The one remaining hit, the entry that lists the banned words
while describing the rule, now quotes each word so the checker reads a
mention as a mention. This file: 0 fail, 35 warn.

**Close of session K, 19:50.** Mark: "push the goldshell commits." Pushed:
`origin/main` at `a883296` with the three tags. The rest of the evening
went to work outside this repo (the Mac-to-Windows SSH star and the
style checker), recorded in the working root's notes rather than here.
Left for the next session: watch the APW3++ reset count against the
brick's zero; the first freeze on the 5-minute ladder is the test of the
new timings; then the 0.5.0 plan for other models, the Linux installer,
the other plug drivers, event-log rotation.

## 2026-09-12 evening to late night, session L: the 0.5.0 plan redirected into holds and planned power, then built unattended

**Mark's brief.** "load the goldshell project", then "start executing 2:
0.5.0 plan. Any questions?" The 0.5.0 plan on file was the other-models
work. The first framing question (cut it into one release or two) never
got its answer, because Mark's next message redirected the whole session:
"since manual power cycling by me or some user causes loss of the board
reset counter, what can we do to preserve that? Is there any reason to
want to put a 'cycle power now' button on the UI? ... The question behind
the question is how to keep that while enabling low-operator-friction hard
power off / on as part of normal daily operations, not just recovery from
'hung'."

**Findings that set the design.** The reset counter is not lost by a cycle:
the service logs it every poll, the trials table sums per-segment
increments, and the 0.4.3 hour tile sums row-to-row increments; only the
firmware's own "since boot" figure drops to zero. The missing piece was
different: a planned outage looks exactly like a freeze to the watchdog
(two minutes unreachable, two failed restarts, the plug cycled about
twelve minutes in, which would turn a deliberately-off miner back on), and
a cycle by the wall switch was invisible to the log. The argument for a
button was not convenience but that every power event should pass through
the one place that records it as the owner's and tells the watchdog.

**Questions and answers.** Daily operation by hand or on a schedule: "c",
both, schedule second. Mark then raised the shutdown case himself: "a need
for a 'shut down now' button that informs the software of an impending
power-down ... so we don't want to have the service reacting to an
unreachable device that's unreachable for physical reasons." That became
the hold, with the buttons and the schedule as three ways into one state.
How a hold ends: the agent recommended explicit release or expiry; Mark
chose the miner's own return ("B. Less user planning and cognitive load
... I'd rather the SW detect that the miner is up"), and floated a
20-minute expiry. The agent argued that under auto-release the expiry only
covers an abandoned outage, so shorter is not safer, and proposed one hour
default with 20 min and 4 h on offer; Mark: "Looks good, build it! See you
in the AM."

**Decisions.** The password rule: Off and Cycle prove the miner's password
by a login through the service (someone who can prove it can already set
725 MHz), On and Hold need none. Plug Off is a hold with no expiry, safe
because the rung already refuses to cycle an open relay. Two good samples
release a hold, a guess to be calibrated. Version 0.5.0 takes this; the
other-models work moves to 0.6.0. Spec: `docs/power-hold-proposal.md`.

**Built, test-first, in a worktree, unattended:** the hold on the watchdog
(seeded from the event log like the caps); `Miner.verify_password_hex`;
`gbox/power.py` with `PowerControl` (off, on, cycle in a thread) and
`Scheduler` (edges only); three POST routes and the health fields; the
page's Power block, dialog path and hold banner, with the interventions
parser reading the new lines; `gbox hold`, `gbox power off`, `gbox power
on`; the `schedule` block validated at load and ticked by the poller.
Every step: failing test, then code. Suites at the docs step: 260 Python
and 39 JS green.

**Left for the morning, on purpose.** The live service was not restarted
and `main` was not touched: the watchdog was the thing recovering the
miner overnight (five freezes that day) and new watchdog code should not
take that duty over while nobody is watching. Deploy is a `--ff-only`
merge, the tag, and one restart. Then the live done-when with Mark's
hands: Hold 20 min and Release, Off then On on the real miner, the log
lines and a reset tally that does not drop.

**Addendum, 2026-09-13 afternoon: deployed.** Mark, after a morning of
data: a freeze at 06:46 and the plug's cycle at 07:00, after which chip 8
threw about 1,040 bad nonces and the board reset 34 times in the first 35
minutes at 575 MHz, then ran normally for seven hours. His reading was
"Chip 8 is failing slowly"; the log's reading was one bad cold start
against six normal ones that week and no upward trend in the steady-state
rate, with the next cold start at 550 MHz as the test. He had already set
550 MHz at 14:20. He asked for the record to be completed (a pool-port
change and its correction, posted as event lines), then: "deploy 0.5.0
now, merge and restart the service." Fast-forward merge, tag `v0.5.0`,
worktree removed, one restart at 15:33; the start line reads 0.5.0 and
the caps carried over. The live done-when with his hands is still open.
Also found from the same log: the ladder's second soft restart lands about
10 minutes after the first, not 5, because judging needs a full fresh
window after the gap; a fix was proposed, not built.

**Addendum, 2026-09-13 late afternoon: 0.5.1, release means hashing.**
The live done-when, Mark's hands: Hold 20 min (logged, auto-released),
Off with the password (logged, relay open), On. "The unit is acting
weird: low wattage, both red LEDs on, not hashing. I think it needs
another power cycle. What do you see from the LAN?" The log: controller
up and answering, clock 0, chip temps 0, board sensor -150, fans winding
down, 9 W at the wall: the hashboard never came up after the power-on.
The hold had released on two HTTP answers. Five minutes later the older
stall rule (accepted frozen) sent a soft restart and the board came back
with it, at 550 MHz. Mark: "fix the release rule so it needs hashing."
Test first (a boardless controller answering with a zero hashrate must
not release; the poller must pass the signal), then the rule: the poller
passes `hashing` (a nonzero 20 s hashrate), only hashing samples count.
Wording changed everywhere from "answers" to "hashes". Patch version
0.5.1. A second finding from the same afternoon: "board absent after a
power-on" is curable by a soft restart, and the stall rule finds it in
five minutes.

**Addendum, 2026-09-13 evening: the Linux installer (plan step 3), and
the 0.6.0 charts agreed.** Mark, after the syslog read: "I think we need
another new graph box, showing the avg error rate per unit time, and the
error rate per worst chip per unit time, vs. the clock rate ... a longer
time horizon - probably 3 days ... Argue with me or ask questions." The
agent argued for bad share over bad count (a faster clock attempts more
nonces), for board resets on the same chart (the 16:02 event was 46
resets and 9 bad nonces; an error chart alone stays flat through it),
for time on x with the clock overlaid, 30-minute buckets, and one
appended all-chips column written board-aware; Mark took all of it, added
"the other graphs should be extended to a min of 24 hours", asked about
polling on a multi-board unit (answer: no change; the per-chip data is in
the request the poller already makes), chose to build this separately
from the other-models work, and said the purpose plainly: "for the human
operator to see when things really went bad on a graph, so she/he can do
something." That is 0.6.0, spec to follow.

First, at his word, the Linux installer for a friend: "I really want to
get the Linux version out tonight." No WSL or Docker on this box, so the
live checklist runs on the Ubuntu 22.04 sibling after a push. Built
test-first: a dry-run test under bash asserts the unit and the command
sequence; the script mirrors the Windows launcher (same command line,
same data rule, uninstall), adds linger with a sudo fallback message, and
refuses non-systemd boxes with a pointer. 0.5.2. Devuan, which Mark may
use for a node: no systemd; the answer would be a respawn line in
/etc/inittab or a runit service directory, and the installer could grow
that as a second backend once there is a box to test it on.

## 2026-09-13 evening, session M: 0.6.0, the operator's view over days

**Mark's brief.** "Spec looks right, write the plan and build it," on
`docs/charts-proposal.md`, itself the product of an argument he asked
for: bad share over bad count, resets on the same chart, time on x, 30-minute
buckets, every chip logged every poll, all other charts to 24 hours. His
purpose, kept at the top of the spec: "for the human operator to see when
things really went bad on a graph, so she/he can do something."

**Built, test-first, in a worktree, in order.** (1) The `chips` column:
every chip's cumulative counts as `board.chip:good/bad`, from the icinfo
request the poller already makes, so a bigger unit costs disk, not
controller time; three older tests that said "watts is the last column"
were updated to the new width. (2) `gbox/series.py`: every row read, the
window cut into buckets aligned to midnight, means over good samples,
counts as row-to-row increments with the counter-reset rule the tiles and
the trials table already use, the worst chip per bucket from the chips
column with the flagged-chips column as the fallback for older rows,
events in the window. One test expectation was corrected mid-way: an
increment belongs to the bucket of the later sample, as the spec says.
(3) `/api/series?hours&bucket`, cached by the log's modification time
and size; three days compute in a third of a second and answer from cache
in two milliseconds. (4) `gbox errors`, which read the live log at once
and showed the morning: chip 8 at 55% with 34 resets in the 07:00 bucket,
46 resets on a flat share at 16:00. (5) The page: the panel renderer
gained long-span ticks with dates at midnight, step lines and bars; the
hashrate, fan, temperature and watts charts draw 24 hours of 5-minute
means when served; the errors section draws three days of bad share,
worst chip, clock and resets with the counts on hover; `/api/log.csv`
gained a `tail` parameter so the tiles stop pulling the whole log. (6)
Docs and 0.6.0. Verified: 42 JS tests, the Python suite by a fresh agent
into an evidence file, and Chrome on a scratch service fed by a new
synthetic three-day log generator (`tests/make_synthetic_log.py`): the
burst, the hole, the clock step, the markers, the day labels, the
tooltip; the standalone file unchanged.

**Left out, said so.** Log rotation goes with the multi-board work
(0.7.0); alerting later; a clock-on-x scatter only if the time chart
leaves Mark wanting it. One slip to own: the series module's tests were
written before the code but ran green on their first run, so there was no
red run for that task.

**Addendum, 2026-09-13 evening: the charts after Mark's first look, and a
design review.** Mark, on the live 0.6.0 page: bold the axis titles;
tooltips truncate at the right edge, "they should grow left"; Power at
the wall above the three-day chart so the 24-hour charts sit together;
and the resets panel was "too touchy": hovering should read resets from
anywhere in that panel, with "some slight visual break between the 3
sections" and the hovered one emphasized. Built and merged as web-only
changes (no restart): axis titles bold, tooltips flip left, section order,
panel separators, the hovered panel lifted with its own tooltip. Then
"Run Claude Design to see if there are any other visual improvements":
a canvas with three directions for the errors chart drawn in the page's
own tokens, each with its motivation and tradeoff. A second look at the
artboards caught two false claims in one option's note and a crowded
marker pair, fixed before Mark saw it. Mark: "I accept Main": alarm
bands on bad half hours, a three-fact strip, worded markers, the clock
as a block. Built the same evening; the first live look found the clock
fill closing across log gaps into a wedge and the watchdog's own restarts
burying the owner's actions among the worded markers; both fixed. Mark
also asked for explainers on Hold and Release "plain to the user or
accessible to the user easily"; proposed on the canvas as a "what is
this?" disclosure under every control plus a Terms section, his pick
pending.

**Addendum, 2026-09-13, 20:15: 0.6.1.** Mark, with a screenshot: "a
numeric bug (maybe from bad data from the controller, but nonsensical
negative temps." It was the controller: with the hashboard absent at
15:46 the firmware reports the board sensor as -150 and the chip as 0,
and a five-minute mean of those rows read -131. The series now treats
that as no reading and the line breaks instead. Same message: "the text
on the display page is too soft; too 'gray' as opposed to black. I have
mediocre eyes with some cataracts, so I think the density should be
increased by 50%," and the legend letters and triangle made bold. Done:
darker secondary and muted text in both color schemes, chart text 12px,
bold marker glyphs. And "I like what I see and the proposal": the
explainers went in as proposed, a "what is this?" disclosure under every
control with the Hold and Release wording from the canvas, title
tooltips on the power buttons, and a Terms section. Patch version 0.6.1,
one restart. Verified on the live page.

## 2026-09-13 late evening, session N: 0.6.2, the ladder's second rung on time

Mark: "pick up goldshell project", and from the next-actions list "1 and
4": the ladder timing and the housekeeping (a picker; confirmed which
numbering he meant before touching anything). The item on record said
the second soft restart landed about 10 min after the first, not 5. Read
from the code and the 09-12 18:16 freeze (attempts at 18:17:59 and
18:27:30, cycle at 18:27:46): after a restart every rule waited for the
5-minute gap and then a full 10-sample stall window of fresh samples
before the 2-minute unreachable rule got a look, so the plug reached a
frozen controller at about 12 min where `power-cycle.md` promised 7. Two
readings of the fix were put to Mark: the 2-minute window strictly after
the gap (about 9 min, as STATUS had worded it) or a window that may reach
back into the gap (7 min, the documented intent; same evidence, two
minutes sooner). Mark: 7. Built test-first in a worktree: the unreachable
rule reads its own window once the gap has ended; the stall rule still
needs a full window after the gap. Two further decisions made and flagged
rather than asked: once two restarts have failed, `after_minutes` is
checked on every dark sample (an `after_minutes` longer than unreachable
plus the gap used to wait a whole extra gap for the next restart's turn),
and a rung refused once in an episode is not asked again until that turn
(self-review caught the plug being queried every 30 s for the rest of an
outage). Found on the way: the old power-rung tests still read as if
`after_minutes` were 15 and passed only because the slow ladder overshot;
they now say their timings. 46 watchdog tests, 320 Python green with the
JS inside; fast-forward merge, tag v0.6.2, service restarted 20:58:59 and
`/api/health` answering 0.6.2. Not pushed (Mark's call). Not yet seen on
a real freeze. Housekeeping: the stale brief at the bottom of STATUS.md,
still describing 0.3.0 and plan step 3 as next, replaced by a pointer to
the top; the ladder-timing item retired. Chip 8 checked from the log:
clean at 550 MHz since 16:30. Left for later: the Ubuntu installer check
(needs the sibling machine), 0.7.0.

## 2026-09-13 night, session O (unattended): the capture request and 0.7.0 gate 1, the model seam

Mark: "pick up the goldshell project, find the next thing in status.md."
The list's first three items were watching (the first freeze on 0.6.2,
chip 8, the Ubuntu installer check, which needs the sibling machine); the
next buildable item was 0.7.0. The session proposed splitting Part C of
the 2026-09-12 plan into three gates (the model seam and plan dialects;
per-board sampling with `boards.csv` and per-board panels; `gbox discover`
and log rotation) and said out loud that all of it is built against
another lab's notes, not a unit. Mark: "what would I need to tell a dev
with an SC Lite about what to capture for Devs and Minerinfo, and how to
capture it? Write an MD file about that ... Go ahead and start with Gate 1
overnight. I'm off to bed."

**The capture request** (`docs/capture-request.md`, committed on main):
seven read-only requests, one at a time with a pause (the token race and
the burst crash), for bash and PowerShell 7; the token from the stock
UI's browser storage or from the encrypt one-liner; pools, wifisetting
and the syslogs deliberately left out; the MAC in `setting.name` replaced
before sending; the debug-lock 401 and the Bearer-or-bare header as
findings to report. Written so Mark can forward it as is.

**Gate 1, built test-first in a worktree, not merged:** the model table
grew a capability profile per row (plan dialect, per-board source,
whether `/dbg/` answers, fan target, what the temperature target is) and
`profile_for` gives an unknown model the SC-BOX's path with every
optional capability off; `/api/health` carries it and the page shows one
line under the title. The plan string is parsed in its three dialects
from the string itself, and the clock control now rewrites only the MHz
token, so a unit never receives a plan in a form it did not write. Two
decisions made without asking, both flagged: string-driven parsing rather
than table-driven, because the table comes from notes and the string is
what the unit wrote; and no conversion of the SC Lite's integer volts,
because nobody has confirmed they are millivolts and nothing needs to
know. Synthetic SC Lite fixtures, marked as such, exercise the seam.
336 tests green (16 new); the unknown-model banner was checked in Chrome
on a scratch service against the fake miner reporting a KD-BOX. The
merge and the service restart were left for Mark: nothing in the gate is
worth a night-time restart of the live watchdog he did not ask for.
Found on the way: `pyproject.toml` still said 0.6.1; fixed to 0.6.2.

## 2026-09-14 afternoon, session P: the night's freezes read, the board that drops, resets made visible and named, gate 1 in

Mark: "have a look at the overnight run ... 7-8 adverse events over 4 hours!
See if you can find any clues what's going wrong during the night time."
From the service log and six timed reads of the miner's own logs (they
persist across reboots and covered the night): seven controller hangs
between 00:42 and 04:07, each with no log line at all, the meter falling
from 191 W to about 50 W within one sample, and six of the seven within
thirty minutes of a boot (10 of the 23 hangs on record). The 0.6.2 ladder
did what it promised: second restart five minutes after the first, plug
cycle about 7.5 minutes after the last good sample. One cycle at 02:44 was
followed by twenty minutes at 43 W with no boot. The cgminer log's
hottest-chip figure, unnamed, peaks at 93 C. Software explains none of it;
the evidence points at the power path, and Mark, who knows the APW3++
holds its output up for most of a minute after the cord is pulled, chose
to swap back to the 360 W supply and asked for the plug's off time to go
from 15 s to 120 s. Done in config.json at his word, service restarted.

While reading, the hashboard dropped off at 13:00:39 with the controller
alive (9 W); the watchdog's first soft restart did not bring it back, the
stall rule's second did, and a 64-reset cold start followed. After the
PSU swap the board failed to start at all ("Write Chip0 Reg 4 Failed",
"Init failed 5 Times"; both red LEDs on); a soft restart cured it. Third
time for that signature across two supplies: the board's power-up is
what is marginal, not one PSU. On the 360 W unit the miner draws 161 W
against 191 W at the same hashrate. Mark also fitted a larger external
fan; the note to him: the controller holds the board sensor at 65 C and
gives most of any extra airflow back as slower internal fans.

Mark: "the 64 restarts shown on the 3-day graph don't show in the
13.00-14.00 timeframe in the 24-hour graphs. This is misleading." True:
the 24-hour charts had no resets series; the 5-minute buckets carried 7,
15 and 42 and nothing drew them. Built: a resets panel under the fans
chart when served, the alarm band rule made one function and applied to
every served chart, reset counts in the tooltips. Then: "Shouldn't we
define a Reset? ... How is a Restart related to a W watchdog event?"
Proposed and built the same afternoon: one vocabulary (a board reset is
the miner reinitializing its own hashboard, counted, drawn as bars, never
a marker; a soft restart is W or your ▼; a power cycle is P), a key under
every served chart from one function, marker hover titles that lead with
the kind, a "what are board resets, soft restarts and power cycles?"
disclosure under two charts, Terms entries for the two actions. Then the
tiles: the hashrate unit beside its number, the board sensor and fan
target readings bold. Each was web files only, tested under Node,
checked in Chrome on a scratch service with a copy of the day's log, and
merged live without a restart.

Gate 1 (the model seam, from the night before) was rebased onto those
three commits, two one-line conflicts resolved, 336 tests green, merged,
and the service restarted on it; the live page shows no banner for the
SC-BOX and the health endpoint names its profile. Verified today on the
SC-BOX: the firmware exposes no per-chip temperature anywhere (icinfo
temp 0.0, the cgminer API's temp_max 0), so Mark's asked-for per-chip
temperature columns cannot be filled here; the plan for gate 2 was
revised with him (AskUserQuestion): every PGA block of minerinfo, the
hottest board named in the tile, chip columns behind a flag, built
against a capture his friend with an SC5 Pro II (four boards, on which
0.6.2 read only the first) is asked for today. The gate 2 plan:
`~/.claude/plans/cuddly-sleeping-abelson.md`.

## 2026-09-14, evening: session Q, the context-budget controls (tooling around the project, not the project)

Mark: "pick up goldbox project." The status doc's item 0, set by Mark at
the end of session P, was tooling: the ten-hour session had reached 57%
of its context window, with screenshots at 28% of it and file reads at
16%, and he had asked for controls rather than another reminder. Built
in the working root, outside this repo: three PreToolUse hooks that deny
a Chrome screenshot without a reduced scale, an unbounded Read of a long
file or PDF, and a shell command that would print a long file whole; a
shared helper; 34 tests, run green. Two decisions made without asking
and flagged: the hooks fail open (a bug in a budget guard must never
block every read, the lesson of the earlier attic hook), and they are
wired in exec form straight to the Python interpreter, no shell wrapper,
so the wrapper's Windows failure modes cannot recur. Verified through the
real launcher, not a piped payload: all three denied their probe calls
live, mid-session. The five usage rules went into the shared AGENTS.md
under session hygiene. Left for Mark: switching off the two unused
connectors, because the only per-server switch is an interactive menu and
the settings key would switch off every connector for the root. Mark
switched the two off in the claude.ai console himself and kept the global
switch on.

Then: "what could we do to limit the high token count from
claude-in-chrome ... fix the present to fix the future." The answer was
that the cost is pixels, not tool definitions: a full-HD screenshot is
about 2,800 tokens, the same at half scale about 700, a 1280 by 800
window at half scale about 340. Four levers proposed in order of size:
browser verification through a subagent so the images never enter the
main window; a smaller window before page checks; a per-session budget
of twelve screenshots and zooms enforced by the hook, with an override
only at Mark's word; a cap on the zoom region, since a zoom of most of
the page costs as much as an unscaled screenshot. Two things considered
and declined: silently rewriting a missing scale to 0.5 (the refusal is
what makes the agent ask whether an image is needed at all), and turning
the Chrome extension off per project (gbox needs it). Mark: "do the
things as you recommended." All four done: two rules, two hook
additions, both verified live. Then, at his word, the two things that had
been offered and held back: a fourth hook for the PowerShell tool, whose
`Get-Content` path the Bash hook could not see, and a cap on command
output in the settings (12,000 characters, down from 30,000), which is
the only guard for commands whose output size no text heuristic can
predict. 67 tests; both verified through the launcher. No change to gbox;
the miner was not touched; still 0.6.5 live. Gate 2 still waits for the
SC5 Pro II capture.

## 2026-09-14, later evening: session R, the cold start measured, the thresholds made absolute

Mark: "load the goldshell project, check the context after all is
stable." A cold start under the new controls, read as an experiment on
them: the ground-truth files were read in ranges (machine notes by
section, the status doc's top block and tail, one evolution entry), the
Bash hook denied one attempt to print the project AGENTS.md whole, and
the service was checked through its health endpoint rather than a
browser. Findings, from `/context` after the load: 85k of a 1m window
in use; the MCP tool definitions that had cost 79k before the session
Q change are now listed but deferred, and the two connectors Mark
switched off remain only as sign-in stubs of a few hundred tokens, not
worth chasing; the fixed cost before the first message is about 51k,
5%, and none of what remains is the owner's to trim (the built-in tool
definitions are the largest part). The loading itself was 36k of
messages. Loading the six core browser tools into a window costs about
4k, the screenshot tool alone over 2k, which is a second reason, beside
the pixels, for the rule that browser checks go to a subagent.

One question came out of the numbers: the session-hygiene thresholds
were percentages (checkpoint at 50%, restart at 70%), and on a 1m
window those are 500k and 700k, past the point where the previous
night's session had visibly drifted at 57%. Proposed an absolute figure
for the first checkpoint. Mark: "use 250k as the first checkpoint
threshold", then "update the 2nd threshold too": 350k. Both are now in
the shared AGENTS.md table with the percentages they replaced, and in
the project memory. Mark then had main pushed (the session Q entry) and
the status doc updated. Nothing in gbox changed; 0.6.5 live, 212
samples, one error, eighteen restarts and seven cycles counted from the
last day, no hold. Gate 2 still waits for the SC5 Pro II capture.

