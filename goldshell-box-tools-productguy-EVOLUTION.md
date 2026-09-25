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

Then the session's real question. Mark: "I'm troubled by that very high
max chip temp that you saw early this morning of around 90-93C. Are we
capturing the max chip temp ... where could we put that to make it
obvious as a Danger flag ... do you think that very high chip temp is
maybe chip 8 with bad thermal paste? ... Reason this out, recommend."
Two read-only passes over the code and the data first. Finding one: the
service captures no hottest-chip figure at all. The field the tile calls
chip temperature is identical to its twin in every sample of three days,
quantized to whole degrees, never above 75, and the per-chip temperature
fields the firmware exposes read zero for all sixteen chips. The 93 lived
only in the miner's cgminer log, read by hand overnight and never saved.
With Mark's word, three reads of that log between the service's polls
(the second wasted by a parser anchored on a bracket the lines do not
start with; owned and recorded). The line, every five seconds: chip
average and hottest chip, no chip index. Over 15,333 lines and 17 boots:
the hottest chip sits a fixed 11 degrees above the average, the same
after boots and at steady state, at both clocks, on both supplies; the
readings at 90 and above are 74 single five-second samples on a level
that holds at 81 to 82; the PSU and fan change bought about 4 degrees.
Recommendation, with the reasoning written into the plan: do not
re-paste. A failed thermal interface shows twenty degrees or more and
drifts; eleven and steady is the position on the board. The trigger for
hardware work is the capture below showing the offset drifting up or the
sustained level above 90, neither visible now. The one pushback on the
ask: a since-boot peak as the danger flag would read 92 within an hour
of every boot, so the flag sits on the sustained level (median over five
minutes) with the peak beside it and the since-boot highs kept as he
asked. Design: a log read every five minutes as a fourth serialized
request, three appended columns (peak, level, chip average), thresholds
85 and 90 on the level from this unit's own distribution, the tile
renamed Hottest chip, a red series with a peaks band on the temperature
panel. Mockup as a design canvas with two tile directions and the panel,
in the dashboard's own colours. Mark: "I accept A," then asked that the
board trace not be red beside the hottest chip and that accessibility
principles apply. The triplet was run through the palette checker on
both surfaces: board blue, chips average amber, hottest chip red and
thicker, every line named at its end so identity never rests on colour.
The build is a full gate and goes to a fresh session from the plan;
this session also found the essay had not been touched since the ninth
(no rule required it), had its second half drafted from this log, and
committed it with the README's summary paragraph.

## 2026-09-14, night: session S (unattended): 0.7.0, the hottest chip captured

Mark, after the plan and the mockup of session R: "mockup looks good
including the revised colors. Build it. Go as far as you can with my 80%
remaining daily credits. Be frugal and do all the good token-saving
behaviors. See you in the AM." An unattended run, so the framing was
restated up front and the stop-and-wait conditions written down: no push
(that is at his word), no clock or settings change on the miner, no poll
cadence beyond the plan's fourth request every five minutes; the
pre-authorized list was the worktree, the branch commits, the
fast-forward merge, the service restart and a subagent browser pass.
Pre-mortem: the live log line not matching the fixture, a bad restart, and
screenshot spend; the first two were covered by reading the row the
service wrote after the restart and the health endpoint, the third by
doing every page check through a subagent.

Built test-first from the plan, in the order it gave. The parser
(`parse_chiptemps`) matches the log line on the timestamp and the two
temperatures and returns nothing else; the scrub test feeds it a fixture
with a pool-user line and checks that only timestamps and floats come
out. The poller keeps a cursor on the miner's own timestamp, takes only
the last five minutes on the first read after a start, writes the peak,
the median (the level) and the median average on the row that read the
log, and counts a failed read instead of writing an error row. Config
gained `syslog_interval` (300, floor 60, 0 off) and the two thresholds;
health serves both. The page gained the Hottest chip tile, the badge on
the sustained level, the three-line temperature panel with the
level-to-peak band and the dashed serious line, a small layout pass so
three end labels never sit on each other, and four new JS tests. The
migration was proved on a copy of the live log: 23 to 26 columns across
26,056 rows, the `.bak` written once, a second run a no-op.

Two things found on the way, not in the plan. Every heredoc through the
shell tool halves a double backslash, so `\\n` in a test string arrived
as a real newline and broke the file; the fix was to write edit scripts to
the scratchpad instead and to write files with explicit LF endings after
`write_text` had turned four files to CRLF and made the diff unreadable.
And one server test, the token endpoint's 415 answer, fails about one run
in three on Windows with a connection reset, on main as much as on the
branch; it is older than this change and was left alone and noted.

Verified, in this order: 354 Python tests and 55 JS tests green on the
branch; the migration on a copy of the live log (23 to 26 columns across
26,056 rows, the `.bak` written once, a second run a no-op); a scratch
service on the fake miner serving the thresholds in health and the three
values in 48 of 289 buckets of the series; after the fast-forward merge and
the restart, the live service up as 0.7.0 with the event line for the
header update and a first row of peak 87, level 76, chip average 66 (the
tile had been saying 65); then a subagent's browser pass in a fresh tab at
1280 by 800 that read the tile ("76 °C", "peak 87 · chips avg 66 · 58.7 °C
board sensor", the since-boot line), the badge "hashing", the five end
labels, the red and amber paths, the band, the dashed serious line with its
label and the four new key items, with no console errors. The red series
was one bucket wide at that moment, so how it draws over hours is the
morning's look. Two browser passes were needed: the first met the scratch
page's login modal and would not type even the fixture password, a rule it
holds without exception and was right to; the live page had its token in
local storage, so the second pass needed no login. The security pass ran
once at feature-complete, the policy default, with no findings: the new
surface is a regex parser over a miner response, three numeric columns, two
config integers and page code that renders numbers.

Left out on purpose: the essay chapter for 0.7.0 (item 2 of the list,
Mark's read first), the push and the tag on GitHub (his word), and the
flaky token test. Gates 2 and 3 are 0.8.0 as the plan recommended.

## 2026-09-15 morning, session T: the night's incident read, the P and H row, 0.7.1

Mark, on waking: "We had freezes & 2 power cycles and a reset storm between
06.45 and 07.45 today. See what you can learn." And: "would you add a P and
H row (powerplug codes) _above_ the 24-hour graphs? The graph tops only
currently seem to show W and S, but not P or Hold ... Plan it."

The incident, read by a subagent from the two logs and returned as
conclusions: one real freeze at 06:31 (37 W, the hung-controller
signature, nothing unusual before it); the first plug cycle at 06:40 did
not boot the controller at all (12 W for 25 minutes, below even the hung
level) and the ladder waited out its settle gap before the second cycle
at 07:06 worked; a storm of 61 board resets eight minutes after that boot,
then the board taking work without hashing it for 17 minutes until the
watchdog's stall rule sent the soft restart that fixed it. Two things
checked on purpose: the new five-minute log read is not the cause (every
freeze onset all night was 210 to 240 s after a read, never within 60, and
126 of 126 reads succeeded while the miner answered), and the hottest chip
showed nothing before the freeze. One thing the incident exposed: the
miner's log survives a power cycle, so the first read after the 07:07 boot
reported the run before the freeze (level 77 with the board at 38 C).

The chart request, planned from the code: P and H were already drawn, on
the one glyph row inside the plot's top edge, and were buried under the
string of "restart attempt failed" Ws a freeze produces (46 in the log
against 15 cycles). Design: a second row above the plot for P and H, one
letter per run of close marks on each row with the lines kept, the first
panel's unit label and right-axis title moved up with the headroom, and
the legend above the fans chart corrected (it still said chip temperature
and board sensor in the pre-0.7.0 colors; HTML above the SVG, so it
needed correcting, not raising). Three choices put to Mark with
recommendations, all accepted: no glyph for a hold's release, a page-driven
switch counts as P, the three-day chart keeps its rotated words. Mark:
"include the stale-read fix and start the build now."

Also answered from one bounded live read of the log (3.5 MB, 50,857 lines,
0.8 s, only line shapes printed): `C0` on the temperature line is the
chain, and no line in the log names a chip's temperature, so which chip is
hottest is never reported. Mark's idea for a "did the cycle boot?" check on
wall power was sized from the watchdog code as about thirty lines plus
tests; recommended as its own 0.7.2, not bundled, because it changes when
the plug fires. He has an SC5 Pro capture from a friend for gate 2.

Built as 0.7.1 on a worktree branch: `markerRow` and `dropClose` pure and
tested, the two rows in `drawPanels`, `last_boot_ts` in the parser and the
poller dropping readings older than the newest boot line, 357 Python and
57 JS tests green, merged fast-forward, service restarted 09:53, then a
subagent's browser pass over this morning's window: the H at 15:30
yesterday and the two Ps at 06:40 and 07:06 above the plot, the Ws and Ss
inside it, 21 mark lines drawn as 12 letters, the axis labels clear, the
legend right. Not pushed: at Mark's word.

## 2026-09-15 late morning, session T continued: 0.7.2, the boot check; the SC5 Pro II capture read

Mark: "1. push now. 2. Yes. 3." and the path to a friend's SC5 Pro II
capture. 0.7.1 pushed with its tag. Then the boot check as its own gate,
as recommended: after every plug cycle the watchdog books one meter read
`boot_check_minutes` (2) later; under `boot_watts` (20) the controller
never came up, and it cycles again at once, once, within the daily cap; a
second dark result is logged and left to the ladder; a good sample clears
the check; 0 minutes switches it off. The cycle action was pulled out of
the rung into `_cycle` so both callers share it. Config validated, the
ladder block and the page's ladder sentence name it, the power-cycle doc
and the plan's decision row carry the reasoning. Three watchdog tests on
the fake plug object (the dark repeat, the quiet pass and the clearing
sample, the cap and the off switch), one config test, one page test; 361
Python and 58 JS tests green; merged, service restarted 10:20 on 0.7.2
with the ladder block serving the two numbers. Not pushed.

The capture, read with the pool name masked: an SC5 Pro II on MCB_V3_3,
firmware 2.2.0, hardware 30.50.SA, plan dialect millivolts with PV. With a
token that answered every `/mcb/` request, both `/dbg/` endpoints answered
401: the debug lock the SC Lite notes described, on a second firmware
family. `devs` carries everything per board: hashrate and average, shares,
hardware errors and their ratio, nonces, a temperature string, four fan
speeds and the uptime. Nothing in gbox reads `devs` yet (the profile only
names it), so gate 2 as planned around `/dbg/minerinfo` PGA blocks does not
fit this unit; a revision around `devs` goes to Mark for a decision.

## 2026-09-15 midday, session U: the owner's second batch, the token that is the same everywhere, port 4028

Mark: "I have some more data for you from the SC5 Pro owner", a request-headers
dump of `/dbg/minerinfo` from the stock debug page, two screenshots of that
page, and a Grok transcript the owner had asked about calling the endpoint
directly. The dump had no response body and no status code, so it could not
answer the standing question (does `/dbg/` answer on that firmware once the
page is open). The Grok page, read by a subagent, repeated what firmware-api.md
already documents, with one lead: the classic cgminer socket API on port 4028.

What the data did answer came from the token itself. The JWT's payload is
fixed (audience, issuer, id 1, subject "minerd", no expiry) and RS256 is
deterministic, so the agent logged in to the SC-BOX here and compared: the
two units' tokens agree on every visible character. Every Goldshell on this
key hands out the same token. Two consequences, one for the plan and one for
owners. For the plan, the gate 2 revision had listed "a token that differs"
among the causes of the 401; that cause is gone, leaving an unlock call the
page makes, or the token race. For owners, the web password gates nothing
the miner does over HTTP: anyone on the LAN with any Goldshell's token, which
is in every helper repo on GitHub, can restart it or change its pools. The
owner had masked the token's tail before sending it, a reasonable instinct
that the finding makes moot. gbox's own password gate on the power buttons
stays, since it protects the page rather than the miner.

The port 4028 probe on the SC-BOX answered with no token at all: `devs`
carries the same fields gbox reads from `/dbg/minerinfo`, board sensor,
reboot count and overheat included, and the numbers matched the last logged
row. Caveats recorded: the response is the malformed run-together JSON
cgminer is known for, `stats` reports a max temperature of zero, and `pools`
returns the pool user, so it must never be logged. It is a candidate third
board source for the multi-board gate, and would outrank the HTTP `devs`
if the SC5 Pro II answers on it, which nobody has checked.

Mark asked the owner for four things (the status codes from devtools, the
unlock request's path, the minerinfo response text, and the 4028 lines) and
said hold. No code or documentation was changed; the findings sit in the
status doc and this entry until the answer arrives.

## 2026-09-15 afternoon, session U continued: the owner answers everything; gate 2 planned a third time

Three more batches from the owner, in the order they arrived. First the
browser's full exchange for the debug endpoint: 200, four PGA blocks, each
with its own chip and board temperatures, the unit's four fans repeated in
every block, and a status block with voltage and current that the SC-BOX
does not report. Then port 4028's `devs` and `summary`, run on Ubuntu with
the lines from the capture request: valid JSON, four boards, the same field
names as the PGA blocks, no token asked. Then a curl retest the agent had
written for him: the debug endpoint answered 200 with a Referer, without
one, and twice in a rapid pair. The two 401s from his first capture were
one-offs. There is no debug lock on his firmware.

Mark also sent Goldshell's spec sheet, which corrected the plan: the SC5
Pro II is rated 14 TH/s at 3300 W, not the 17 the previous revision had
carried. The owner's unit reads 13.99 TH/s, and volts times amps from the
firmware comes to about 3040 W, which is the DC side of a 3300 W wall
figure at a typical supply efficiency. The unit of the current field is
inferred from that arithmetic and the plan says so. A screenshot of the
stock UI gave the plan names, Hashrate, Low-power and Idle, for levels 0,
2 and 3.

The recommendation changed with the evidence and Mark took it in two words:
one parser for the PGA-block shape, fed by either transport, since the
field names are identical; the service reads port 4028 first, because it
needs no token and cannot hit the token race, and falls back to the debug
endpoint; the page, which cannot open a socket, keeps reading the debug
endpoint. Fixtures credit "a friend's unit". The plan was written as the
third revision of gate 2, with the parser signatures, the totals rule
(hottest board's temperatures stand for the unit, so today's columns keep
their meaning), a `boards.csv` beside the log, a firmware-watts figure
labelled DC and inferred, the two model rows, the fixture list with what to
strip from each file, and eight test-first tasks. One item is deferred to
Mark: the page's fallback to the HTTP `devs` endpoint, a third parser for a
lock no unit has shown. The API notes got the constant-token finding and a
port 4028 section the same afternoon. The build goes to a fresh session.

Closing the session (15:05): Mark asked what "devs" was about, since three
different things had carried the name. The answer: one cgminer command,
reachable as the debug endpoint's text, as an HTTP wrapper in the stock
UI's API, and raw on port 4028. He renamed the model table's `"devs"` value
to `"http_devs"`, agreed to drop the page's wrapper fallback, and said
build. On a reminder of the two-hour boundary he reversed that in one
line and sent the build to a fresh session. The plan, the status doc and
this narrative carry both decisions.

## 2026-09-15 evening into 2026-09-16, session V: gate 2 built, six tasks of eight

The session opened with "load the goldshell project" and, once the state
was restated, "Begin the build tasks. Delegate easy things to cheaper
stupider agents when you can. I am almost out of Claude credits for 3 days
till a new month. be frugal." Two answers set the shape of the work: skip
the security pass for now, and go ahead with the one live read the plan
needed. Later, after a subagent overran: "Keep the agent limits tight."

The build ran as an Opus session managing Sonnet executors, one bounded
brief per plan task, with every acceptance claim re-checked here by running
the commands rather than reading the agent's prose. Six of the eight tasks
landed: the fixtures and the fake miner's port 4028 listener, both model
rows with the stock plan names and the http_devs rename, the two board
parsers with their totals, the poller with its transport setting and
fallback and the new watts column and boards file, the page with its
mirrored parsers and boards table, and part of the API notes.

Three findings changed the work. The plan placed voltage and current inside
each board block; the capture has them once in the status block ahead of the
first board, so implemented literally the firmware watts figure would have
been blank everywhere, contradicting the plan's own expected number. The
executor caught that and passed the unit-level text alongside each block.
The plan also said the SC-BOX's port 4028 block lacks voltage; it carries
one, reading 0.41 in a unit that is plainly not the millivolts the other
model reports, and it lacks only current and the power identifier. The
consequence is the same either way, no watts figure on that unit, but the
reason is different and now recorded correctly.

The third finding is about the method rather than the miner. A documentation
agent, handed that correction, wrote it up as "both units carry voltage in
the status block", which is false for the older unit over HTTP. The error
was caught only because the claim was checked against the fixture before the
commit. A correction handed to an agent is not a correction until something
verifies what the agent did with it.

Pushback ran the other way once, and it was the right call. An executor's
first draft of the preset list broke an existing test; rather than edit the
test, which the brief forbade, it changed its own code to preserve today's
shape.

What it cost. One executor wrote a correct poller but never reported,
stopping three times with no hand-back at 109 tool calls against a budget of
70 and roughly 230,000 tokens. It was stopped, its work verified here and
committed by hand. Later briefs carried a smaller budget, a hard stop short
of it, and an instruction that an unfinished write-up is a success. A
standing rule for delegated briefs is worth considering: a budget, a stop
below it, and reporting as the first duty rather than the last.

Left undone: four of the five documents in task 7, and all of task 8, which
merges, restarts the service and verifies both pages. The version stays at
0.7.2; 0.8.0 waits for gate 3. Nothing was pushed and the branch is not
merged.
## 2026-09-17 evening, session W: the cold-start hashboard failure, and the settle gap that hid it

This was not a build session. It began on another machine's problem entirely
(blocking Windows 11 auto-restart) and reached the miner only because the
health payload was on screen: seven watchdog restarts today, one power cycle.
Mark: "yeah look into those 7 restarts, but they aren't much worse than recent
days. see if there are any new clues about bad box behavior."

The log reading went to a subagent with a written budget and stop-losses, so
the 6.3 MB telemetry file never entered the main context. Its two load-bearing
claims were then re-verified directly before being acted on, per the rule that
an executor's prose counts for nothing.

**Mark's impression was right, and worth recording as a method note.** Seven is
below the 09-11..09-16 mean of 8.3, and one cycle is the quietest day since
09-11. More usefully, the seven are not seven failures: they are one 95-minute
incident, with sixteen clean hours before it and three after. Counting events
per day had been hiding the shape of the thing.

**The finding.** After a power interruption the control board boots and answers
HTTP normally while the hashboard never comes up at all: zero hashrate, both
temperatures reading 0.0, fans spinning down, wall draw collapsing to two and a
half watts. The unit looks alive and does nothing. It stays there until the
stall watchdog restarts it. Five episodes, every one since 09-13, none before,
each beginning within about a minute of a power interruption.

**What it is not.** Every wear metric is flat or better than last week: mean
temperature falling, watts falling, fan speed up 2.6% at matched temperature,
today's error rate the lowest of the week, bad-nonce share at zero, no weak
chip on any day. The 09-13 hashrate step tracks the deliberate clock change
almost exactly. Nothing is dying.

**The awkward part, and the reason this entry exists.** The 6-to-8 minute
episodes and the 22-minute ones differ only because gbox's own `settle_minutes`
of 20 suppressed the stall check after a cycle. The tool was lengthening its
own outages. Worse, 0.7.2 had already added the boot check for precisely this
failure, and today it did not fire: the wall draw was three to four watts at
the moment it was due, far under `boot_watts`, and no event line was written.
Two candidate paths were found in the code - `check()` returns early whenever a
hold is active, before it ever reaches the boot check, and the boot check
itself returns silently when the watts reading is absent or high. **Which one
suppressed it was not proven, and neither was fixed.** `_check_boot` has one
call site and no test coverage. That is the more important defect; the settle
gap is a symptom next to it.

**The decision.** Mark asked for the rationale behind `settle_minutes` 20
before agreeing to change it - "It was pushed out for some reason" - and the
answer was thinner than expected. It came from the power-cycle proposal, where
it sat beside `after_minutes` 15. The 09-12 tuning pass cut `after_minutes` to
5 and `min_gap_minutes` to 5 and left the settle gap alone. An un-revisited
default, not a defended value. The empirical check that settled it: across 25
measured boots the miner is hashing within 5 to 25 seconds of its own uptime,
never longer, roughly a minute of wall clock from the relay closing. A
twenty-minute grace period for a one-minute boot.

Cut to 6 in the live config at Mark's instruction, service restarted, health
confirming. The package default was deliberately left at 20 pending a decision
on whether to follow it in code.

**Also found, not fixed:** the cycle counter disagrees with itself - the log
wrote "cycled #3 today" while the API reported one - and four telemetry columns
(`hot_peak`, `hot_level`, `chip_avg`, and `weak_chips` after 09-13) are no
longer populated, so chip health had to be inferred indirectly.

**Confidence stated plainly:** high on the trend numbers, medium at best on the
cold-start diagnosis. 09-13 is badly confounded - the clock drop, a CSV schema
change and Mark's first use of the off/on button all land on that day, so a new
box fault cannot be cleanly separated from new tool behaviour exposing an old
one. What would settle it is about ten deliberate cycles on a healthy box,
counting the failures. A standing note was added to `AGENTS.md` telling the
next session to read the aftermath of any power cycle rather than wait for
someone to notice a bad day.

## 2026-09-18 into 2026-09-19, session X: the boot check's real cause, 0.7.3, and gate 2 brought forward

Mark: "pick up the goldshell project and look at what's queued. I want to
continue with the work in progress which should be some minor fixes to v0.7.2,
commit that, then go on to the planned v.0.8. Have a look at get familiar. Start
anything you can tell is straightforward and use the new agent delegation kit."

Three things came out of it: the open boot-check defect turned out to have a
cause nobody had guessed, the 0.8 merge path turned out to be blocked in a way
nobody had noticed, and the delegation kit got its first real exercise on
`executor` and `reviewer`.

**The boot check: the cause was neither candidate on the list.** Session W had
left two suspects, a hold blocking `check()` before it reached `_check_boot`,
and `_check_boot` returning silently on a watts reading that was `None` or above
`boot_watts`. Both were plausible and both were wrong. Running the real
`Watchdog` against the fake plug in two scenarios settled it in one go: a fully
dark box writes the boot line and cycles again correctly, and a box that answers
HTTP with a dead hashboard writes nothing at all. The cause is one line in
`observe()` -- any successful HTTP sample cleared the pending check, with the
comment "it answered: it booted". A cold start answers. The check existed
specifically to catch a unit that answers while its hashboard is dead, and it
was cancelled by the very symptom it was watching for, every time. The fix uses
the `hashing` flag the poller already passes.

Worth recording as method: the two candidates in the status doc were reasoned
from reading the code, and the answer came from running it. Fifteen minutes of
probe beat two sessions of hypothesis.

**The counter defect was a labelling bug, and Mark chose the smaller fix.**
`cycles_today()` is a rolling 24-hour window; only the word "today" was wrong.
Reproduced exactly: a cycle logged as "#4 today" read as one cycle eight hours
later, because the earlier ones had aged out. Offered as (a) fix the wording or
(b) build a real calendar-day counter, Mark took (a) in one word. (b) would have
changed what the daily cap means, for a label.

**A hole in a delegation brief, found by the code rather than by the agent.**
The brief named `SEED_RE` as the hazard in the wording change and put
`gbox/web/*` out of scope. The page mirrors that regex with its own copy, which
still required the literal "today", so the dashboard would have silently stopped
classifying power cycles from the first cycle after the change. The executor
did exactly the right thing -- it stayed inside the scope it was given -- and the
defect survived because the scope was wrong. The lesson is not about the agent.
When a brief says "this regex is the hazard", the next question is what else
reads the same text, and the answer has to be searched for rather than recalled.

**The executor's scope-stop was worth more than its code.** Told to stop rather
than widen scope, it stopped on the `settle_minutes` default because one test
asserted 20 and that test was out of scope. There were six failures across four
files, not one. Had it edited its way to green it would have buried that.

**The review found three real defects and overstated one of them.** A `reviewer`
on the parent's model attacked the branch and produced three findings that all
reproduced under direct probing: a live cycle's own log line could be counted
twice by a later seed, an out-of-order seeded entry was never pruned and
inflated the count permanently, and a repeat seed reset a restored hold's
`ok_streak`. It also cleared, with evidence, the change most likely to be
dangerous -- whether gating on `hashing` could restart a healthy miner -- by
finding the `boot_watts` guard that bounds it, and it proved the new test had
teeth by reverting the fix and watching that test alone fail.

Where it overreached: it called the double-count "the overlap that actually
happens (a running service's own lines)". It does not happen. `seed_from_events`
has one call site, at service start, before anything live exists. All three
findings are latent. They were fixed anyway, each with a test that fails without
its fix, but they were not a reason to hold the merge, and the report read as
though they were. Same shape as the calibration note in the delegation kit's own
status file: the numbers were right and the prose ran ahead of them.

**The 0.8 merge path was blocked, and nobody had noticed.** The gate 2 plan ended
with "merge `gate2-pga` to main `--ff-only`". That was impossible twice over:
main had moved three commits ahead, and one of those commits added
`.gitattributes` and renormalized line endings. `gate2-pga` predates it, so
every blob on the branch was still CRLF. A plain rebase failed at commit 7 of 8,
and simply checking the branch out against main's attributes showed the entire
tree as changed -- 559 lines of 559 in `api.py`, 1538 of 1538 in `app.js` -- which
committed would have buried eight commits of real work in line-ending noise.

`git -c merge.renormalize=true rebase` is the answer; it normalizes both sides
per blob before merging. Seven of eight commits then applied clean, and the
eighth was an ordinary append conflict in this file, both branches having added a
session entry. It was proven on a throwaway branch first, and the check that it
worked is that `git diff --numstat main` after the rebase is byte-identical to
the diff against the merge base before it. The real rebase reproduced that
exactly, and the rebased branch runs 396 Python tests and 66 JS tests green.

**Version.** 0.7.3, at Mark's call, over the argument for a minor bump: the event
log's wording and two CLI strings changed, which AGENTS.md makes a bumping
change, but 0.8.0 is reserved for gate 3 and nothing here touches the log.csv
schema or the HTTP API. `/api/health`'s `cycles_today` key was deliberately left
alone so the page and any other reader are unaffected.

**Also declined.** The executor's evidence file was kept out of the repo at
Mark's word; `evidence/` is not a convention here and the repo is public. No
validation bound was added for `settle_minutes`, which remains an open decision.
The security pass stays deferred.

**Verified on the hardware, not just in tests.** The standing post-cycle check in
AGENTS.md had not been run and was overdue. One cycle since the `settle_minutes`
change, on 09-18 at 21:41: no cold-start failure, hashboard up inside a minute,
and no watchdog restart afterwards -- the regression the shorter settle window
risked did not occur. Health flat to slightly better across hashrate, watts and
temperature, with restarts per day down from 8.0 to 1.8. One data point, and it
never entered the failure mode, so it tests the shorter gap and not the fix.

## 2026-09-19 midday, session Y: what the last checkpoint got wrong, and the hardening that came out of checking

The goal, in the owner's words: "pick up the goldshell project and continue with
next actions in the plan. Stop at the end of the 0.7.x activities, before
starting the 0.8 work."

**Most of the queued list turned out to be already done.** The previous session
wrote its checkpoint at 00:30 and then, at the owner's word, pushed and
restarted the service without refreshing it. So the file still said "not pushed"
and "service NOT restarted" while the tag was on GitHub and the live process had
been running the new code since 09:56. Reading the machine rather than the file
is what caught it: the remote ref, the process start time and `/api/health`
settle the question in three commands, and none of them agreed with the
checkpoint. The lesson is not that the previous session was careless -- it did
the work and said so in chat -- but that a checkpoint written before the last
two actions is a checkpoint that lies, and the cheap fix is to write it last.

**Three more claims in that checkpoint did not survive being checked**, which is
the substance of this session. An "unexplained data defect" in the telemetry
columns was not a defect at all: those columns are written only on the rows where
the syslog read falls due, one in ten by design, and the column that had gone
quiet did so because the box stopped producing weak chips after the clock drop. A
list of four untested branches was wrong about two of them, which had been
covered for some time. And a note to correct a `--ff-only` instruction pointed at
the wrong file: the plan it named had no merge recipe at all, while the flat
instruction that would actually mislead the next branch was in this repo's own
`AGENTS.md`. Three for three, in a checkpoint whose own closing lesson had been
that a reviewer's prose can run ahead of its numbers.

**What was built.** Two small pieces of hardening, both found by reading code
rather than by any failure. `settle_minutes` was the only value under `power`
with no validation bound, and it is the one that suppresses the stall check; at
20 it had hidden a dead hashboard for 22 minutes two days earlier, so a typo of
600 would hide one for ten hours. It is now bounded. Separately, the boot check
returned silently when the plug cannot measure watts, which produced exactly the
same empty log as a box that booted cleanly -- and the standing check in
AGENTS.md asks a reader to tell those two apart. It now says which it was. Three
tests, each confirmed to fail with its fix reverted, which also closed the two
branches that genuinely lacked coverage.

**A risk recorded rather than fixed, deliberately.** `boot_check_minutes` is 2,
and the CLI tells the owner to allow two or three minutes on units other than
the one this was built against. The check cycles anything drawing under
`boot_watts` at the two-minute mark, and a cold start draws a few watts. On the
box in front of us it is harmless -- 164 W by then, measured -- and it becomes
live the moment a slower-booting model is supported. Two fixes were put to the
owner, a longer default or a second confirming reading, with the second
recommended. Changing when software cuts power to hardware is his call, not a
routine judgement to make on his behalf, so the branch carries the note and not
the change.

**Scope held.** Gate 2 task 7 was raised twice as the next thing to do, once by
the previous session's handoff and once in conversation, and it is 0.8.0 work by
its own plan while this session's instruction was to stop before that. It was
flagged as a contradiction and left unstarted rather than resolved by inference.

**Delegation.** Two scanner subagents: one read the telemetry, one read a
1013-line session transcript to recover the handoff. Neither put a raw line into
the main session. The second found the stale checkpoint on its own and said so
plainly, which is how the discrepancy surfaced at all.

## 2026-09-19 afternoon, session Y continued: the meter demoted, and a live service stopped by accident

The session's second half started from one question the owner asked about a
bound added that morning: should `settle_minutes` not simply have a minimum of
two minutes? Checking it turned up the reason a flat minimum would have been
wrong, and then a principle that reached further than the question.

**The finding.** The settle gap was timed from the moment the relay OPENS, and
the plug call blocks for `off_seconds` while the relay is open. So `off_seconds`
was spent inside the gap before the miner had begun to boot: at the owner's
`off_seconds` of 120, a `settle_minutes` of 6 was really 4, silently, and
disagreed with what the seeding path recomputed after a service restart. The
owner's verdict: "that seems like a stupid design decision." It was, and a flat
floor of two minutes would have compensated for it rather than fixed it.

**The principle, which redirected the work.** In the owner's words: the power
meter on the plug has to be an add-on value for deciding whether the controller
and hashboard are up, because a plug with a meter cannot be counted on; deciding
whether a restart worked must start with network behaviour and especially the
hashrate. Each function was checked against it rather than assumed. Two already
complied: the sample observer keys off `ok and hashrate > 0`, and the power rung
reads watts only to build its descriptive string. Exactly one violated it, the
post-cycle boot check, and badly -- wall draw was the sole decider, so on a plug
without a sensor the one mechanism meant to catch a cycle that left the box dark
did nothing at all.

**What the telemetry settled.** A question about the right check interval was
answered from the log rather than from judgement: all five known cold-start
failures drew *over* the threshold transiently in their first 30 to 70 seconds
before collapsing, so a check at one minute reads a failing boot as a healthy
one. That is why the interval now has a floor of two, and why a low reading is
confirmed before anything acts on it.

**A pushback the owner accepted.** He asked that a hold release be "confirmed by
wattage, if available". The recommendation back was to log the draw and never let
it gate the release, because the meter had read 15 W for a minute on 09-18 while
the miner hashed above 600,000 -- a glitch that would have pinned the watchdog
down. Confirmed, and the rule is now written into the code comments.

**Verified on hardware, twice.** First a harness driving the real plug driver,
real TCP and a real relay on a spare meterless plug with a lamp on it. Then the
full thing: a scratch service against a fake miner and that same plug, which ran
the whole ladder end to end and exercised the new meterless path through the real
poller and watchdog. The bench run found a defect no unit test would have: the
repeat-cycle line printed its "no meter" note twice. It also showed the restart
ladder can reach its own power cycle before the boot check returns a verdict when
the settle gap is shorter than twice the check interval -- benign, same remedy,
and recorded rather than fenced off with a validation rule that would have
blocked the minimum the owner asked for.

**A real mistake, and what came of it.** Cleaning up the scratch service, a
command that selected processes by matching their command line also stopped the
owner's live mining watchdog. Both run identical command lines, so the filter
could not have distinguished them; it was not a careless shortcut but an
impossible one. Down about twenty seconds, counters intact through event-log
seeding, miner unaffected. The owner asked what would stop a future session
repeating it, and was told honestly that documentation alone was worth perhaps
40 percent, evidenced by four cases in this same session where written-down facts
had failed to change behaviour. So the fix is three-layered: a hook that refuses
the dangerous command shape and names the alternatives in its refusal, a pid file
each service writes into its own data directory so there is a precise handle at
all, and only then the prose. The hook denied its own documentation on first
firing, because the write-up quotes the commands it bans; heredoc bodies are now
treated as data.

Shipped as 0.7.4. A patch: nothing here touches the log schema, the CLI surface
or the HTTP API, and 0.8.0 stays reserved for gate 2 and gate 3, which this
session was told to stop before and did.

## 2026-09-19 evening, session Z: gate 2's docs, and the bug a plain scratch run found

**The goal, in Mark's words:** "pick up the goldshell project, let's build the rest of the things in the plan."
Read as: finish 0.8.0. Gate 2 had two tasks left (docs; verify and ship). Gate 3 (`gbox discover`, log rotation)
has never had a written plan, so it was scoped as the next gate, not squeezed into this one.

**The rebase was a non-event.** The plan and AGENTS.md both warn that `gate2-pga` holds CRLF blobs. It no longer
did: session X had already rebased it past the line-ending commit, so it was simply eleven commits behind main. The
renormalizing recipe ran clean anyway, eight commits, no conflict, and the diff against main was byte-identical to
the diff against the old merge base. A warning that outlives its cause still costs a reader a check.

**Task 7, docs.** `firmware-api.md` needed nothing; sessions U and V had covered it. The capture request now leads
with the two port-4028 reads, which need no token, and "skip this on Windows" is gone: a bash `/dev/tcp` form and a
PowerShell `TcpClient` loop replace it. Both were run as written against the fake miner's 4028 listener before they
went into a public how-to. The README gained a "Which miners" table that claims support for the SC-BOX only and
says what every other row was built from. The plan was asking for an edit to a supported-models table that did not
exist; it was written rather than skipped.

**The finding.** Task 8 calls for a scratch service on the fake SC5 Pro II. Run against the fixtures exactly as
they are, it logged nothing: the capture has no icinfo file, the fake answers 404, and the poller tolerated only a
persisted 401. Every SC5 test had set `dbg_locked_icinfo=True`, so 409 green tests never saw it, and the page side
already swallowed any icinfo failure. On the friend's unit, where icinfo is unproven, a 404 or 500 would have cost
every row. Fixed narrowly: an HTTP error status on icinfo blanks the chip columns only until icinfo has answered
once in the run. The alternative, tolerate it always, was rejected because on the SC-BOX a blank row writes a zero
into `nonces_good`, which `series.inc` reads as a counter reset before adding the run's whole count again. That
claim was first written from memory with the wrong module named, then checked against the code and corrected.
The fix is its own commit so it can be dropped. Known edge, not handled: a unit whose icinfo fails first and
answers later gets one inflated bucket on the errors chart.

**Not done, and why.** The page check of the scratch service did not run. The browser extension was connected to
a browser on a different machine, whose 127.0.0.1 is not this one; the subagent reported connection refused while
`curl` on this machine got 200. Left unverified and said so, not replaced with a weaker check. Merge, service
restart, the live page check and the push all wait for Mark. The security pass stays deferred to 0.8.0
feature-complete, which is after gate 3.

**The machine got in the way.** The first full suite run hung on a `MemoryError` at 192 tests. Commit charge was
60.5 GB against a 60.6 GB limit, with 8.4 GB of it in nonpaged kernel pool after about 39 days of uptime. The rerun,
one module at a time under a timeout so a memory failure would show per module, was green: 409 Python, then 411
with the two new tests across the modules the fix touches, and 66 Node.

**Later the same evening: merged, restarted, and gate 3 planned.** Mark's answers, verbatim: "1. yes, merge and
restart; 2. keep it 3. yes size cap is OK. yes, hold the security pass." The whole suite ran once more on the final
branch (411 Python, 66 Node), main was fast-forwarded, and the live service was restarted by the pid its own pid
file and the port's listener agreed on. About fifteen seconds down. The proof of the restart could not be the
version string, which stays 0.7.4 until gate 3, so it was the new start time, the log header migrated to 27
columns, `/api/boards` answering, and the absence of a "port 4028 closed" line: the SC-BOX is now read over 4028.

The "yes" to question 3 bought one read of the SC-BOX, timed between two polls: `/mcb/status` answers with no
token and carries the model string. So `gbox discover` needs no credential at all. The agent lost the first
reply's body to a path slip between two shells and said so before making a second single read. On rotation, Mark
chose a size cap over monthly files. The agent then changed the simple version it had offered: a bare rename would
blank the 24-hour charts and the trials table at the moment of rotation, so the plan carries the last 72 hours into
the new file, which needs no reader changes. That change is flagged in the plan for Mark, along with the one open
number, the default cap. Not pushed; the page checks still wait for a browser on this machine.

**The page checks, once a browser on this machine was connected.** Mark: "push it; 72h carry-over and 25 MB are
fine. Chrome is running. check it first to verify you can control it." Pushed. Three browsers were connected and
the session still pointed at one on another machine; the one Windows browser was selected on Mark's instruction
and confirmed by device id and by the page reporting its own platform, because the tool's reply named it
"Browser 1", the label the Mac had carried a moment earlier. Both checks were done as DOM reads, no screenshots.
The live SC-BOX page passed: boards table hidden, two fans, plug watts, no stray NaN. The scratch SC5 Pro II page
passed on boards, fans, the DC power figure and the plan names, and failed on something nobody had listed: the
unit's settings carry no fan target, and the page printed "target NaN °C", "undefined °C", and offered a working
"set target" button that would have written a field the firmware lacks. Fixed on the data the unit wrote, not the
model table, and checked again in the real page. Second defect of the evening found only by running the thing
against the fixtures as they are; both had green suites above them. Left on branch `sc5-no-target` for Mark's
word, since merging web files changes the live dashboard at once.

**Merged the same evening.** Mark: "yes, merge and push it." The fan-target fix went to main by fast-forward and was
pushed; no restart, since no Python changed and the service serves the page files from disk. The live SC-BOX page
was read once more afterwards and still showed its 65 °C target and its control, so the fix touched only the unit
it was for. The gate 2 worktree and its branches were then removed, the pre-rebase backup last and at Mark's word,
because its rewritten commits needed a forced delete. Main is the only branch. Gate 3 is next, in a fresh session.

## 2026-09-19 late evening into 2026-09-20, session AA: gate 3, four tasks of six, and two bugs only running found

Mark opened with "open the goldshell project" and one line of scope: "STATUS.md's top block and
`~/.claude/plans/gate3-0.8.0.md` carry everything it needs." The session restated the brief before acting, checked
the repo, the live service and the machine's memory headroom rather than trusting the status file, and offered
three ways in. Mark picked the first: make the worktree, take a baseline, start task 1. The baseline mattered more
than it looked, because the previous session had left one module unrerun and the machine had hit its commit limit
that afternoon; run one module at a time, the suite came back at 411 Python and 67 Node, matching the checkpoint
exactly. That is the first checkpoint in a while whose numbers were right.

**Task 1 corrected a fiction in the test double.** The firmware answers `/mcb/status` with no token, read off the
real unit the evening before, but the fake miner demanded one on that path. Every `discover` test would have agreed
with a fake that behaved unlike the thing it stands for. Three tests now pin the corrected shape, including one
that keeps the fake strict about a *wrong* token, which nobody has ever tested against the firmware and which is
labelled unverified in the test, in the commit and in `docs/firmware-api.md`, whose "all need the token" heading
was simply wrong.

**Task 2 built the module, and a test caught the design.** The first cut matched the skip list on the host part
alone, which is right on a real network where every address is bare, and wrong in the suite, where two fake miners
share a loopback address and differ only by port: skipping one skipped both. The rule became host and port, with
either side lacking a port matching any port on that host. One reading of the plan needed a decision. "More than
1022 hosts (/22) is refused" can mean a /22 is the widest allowed or the first refused; the session implemented the
former, said so in the commit and asked. Mark: "1, carry on with task 3."

**Task 3 found a bug that was not this gate's, by running the command instead of only testing it.** The plan carried
that instruction forward from the previous session, and it paid twice. Pointed at the SC5 Pro II fixture, the new
command died with a character-encoding error and lost the row. The cause was old: that unit's model string holds a
Unicode roman numeral, recorded in the model table as the exact bytes from a friend's machine, and a Windows console
cannot encode it. Printing it raised `UnicodeEncodeError`, which is a `ValueError`, which the command line already
caught and turned into a terse error and a non-zero exit. The same path runs under `status` and `chips`, so those
commands had been failing that way on Windows against that model for as long as the table has held the string, and
no test saw it because tests print to a buffer that accepts anything. Output now falls back to the stream's own
encoding with replacement, and three tests hold it against a real code-page stream. The same run prompted stripping
non-printable characters and capping field lengths in the probe: a sweep asks every address on the subnet, anything
can answer port 80, and whatever answers gets printed to a terminal.

**Task 4's margin came from real data, not from the plan.** `rotate` carries the recent rows into a fresh file and
archives the whole old one, so no reader changes. Run against a copy of the live log, never the live one, the
24-hour series was identical across a rotation and the 72-hour series was not: of 145 half-hour buckets exactly one
differed, the oldest, with 29 samples where it had had 60. Carrying exactly the window leaves a reader asking for
exactly that window with a half-full bucket at the edge, and the three-day errors chart asks for exactly 72 hours,
so the plan's own done-when was failing by two rows. A two-hour carry margin fixed it and both series now match.
The session also widened the plan's `PermissionError` to `OSError`, because the same failure is a different
exception on macOS, and because catching the base class is what let the failure path be provoked by standing a
directory in the working file's place, in a suite that contains no mocks and should not gain its first one here.
Reported and accepted in the same breath: the trials table reads the whole file, so it drops from 93 segments to
19, 75 of them older than the carry by Mark's earlier choice, and the oldest survivor clipped at the boundary.

Left for the next session: the wiring, the release, and the one security pass. Nothing is wired yet, so the running
service is unaffected by any of it. No sweep was ever run against the real network, no test touched the live data
directory, and the service that has been logging since the previous evening was not stopped or restarted.

## 2026-09-20 morning, session AB: gate 3 finished, and a security pass that found the sweep pointing at a VPN

**The goal, in Mark's words:** "pick up goldshell project", and a minute later, while the baseline suite
ran, "verify the service started". No other instruction all session. The previous checkpoint named the
next action precisely enough that none was needed: finish gate 3, task 5 then task 6.

**The service had restarted itself, and that was worth saying out loud.** The machine rebooted at 10:01
after the previous session's memory trouble, and the service came back at 10:06 on its own. Verified
rather than assumed: the pid in its own data directory names a live process, that process owns the
listening socket, the sample count advanced over seven minutes, and the log grew. The reboot also cleared
the leaked kernel pool that had been squeezing the last session, so the suites ran one module at a time
out of caution rather than necessity.

**Task 5 was the wiring, and it was uneventful, which is what a plan is for.** The config block, the cap
checked before every append for both CSVs, the event log carrying lines rather than hours because that is
what the watchdog seeds its daily caps from, the health field, the page line. The continuity test is the
gate's done-when made executable: over a seeded six-day log, the 24 h and 72 h series and the trials
segments inside those windows are identical before and after a rotation. It compares the windows the
charts draw and not the whole-file trials result, because the addendum had already established that the
whole-file result legitimately changes.

**One thing was built that the plan did not ask for, for the reason the plan had already accepted once.**
Task 4 closed the crash window for `log.csv` by promoting a stranded working file at service start.
`boards.csv` and `events.log` had no equivalent, so the same crash would have left the only copy of the
carried rows in a working file while a fresh empty one grew beside it. Each now finishes its own
interrupted rotation at its next write.

**Then the security pass, and it earned its place twice over.** It ran as a fresh-eyes review over the
whole gate, and the two findings that mattered were both things the tests could not have caught, because
both were about the world outside the test directory.

The first: `gbox discover` with no arguments asked the default route which network to sweep. That is right
on an ordinary machine and wrong on this one, where a VPN client owns the default route, so the answer was
a public address belonging to the VPN provider. A bare `gbox discover` would have sent one request per
address into a stranger's network through the tunnel, while the miner sat on the LAN two feet away. This
was one command away from being found at any point in the gate and nobody ran it, because the plan
reserved the live run for Mark and the agent honoured that; the sweep was never run, but the address it
would have swept could have been printed at any time. The command now discards a routed address that is
not private, falls back to the machine's own private addresses, asks for `--subnet` rather than guessing
when there is none, and prints the subnet and the address count before it starts.

The second: rotation could run on every poll. If the carried window does not fit under the cap, the fresh
file is born over the cap, so the next poll rotates again, and each rotation replaces the archive with the
rows it has just carried. Everything the first rotation set out to preserve would have lived for one poll
cycle. The settings that reach it are ones the validator accepts. It was reproduced before it was fixed,
three rotations in three polls, then fixed by stopping after a rotation that did not help and saying which
of the two settings to change. The distinction that makes that safe is between a rotation that could not
run, which is transient and must be retried, and one that ran and did not help, which must not.

Four smaller findings were fixed the same way: any range on earth was an acceptable `--subnet`, redirects
were followed so one device could point the sweep elsewhere, the skip of the configured miner was defeated
by a scheme or a path in a config value nothing validates, and deferred-rotation event lines carried the
Windows exception text, which names the data directory and the account, into a log the dashboard serves.
Four more were written down and deliberately not built, each with its reason, in `docs/security-notes.md`.

**What was left for Mark, and why.** The branch is not merged and not pushed. The live service still runs
the previous version. The one live `gbox discover` run is his, as the plan always said. Merging is his call
for a reason that is easy to miss: the running service serves the web files from the main checkout on every
request, so a merge changes the dashboard in front of him before anything is restarted.

**Housekeeping worth recording.** Two real addresses reached code comments, a test and a commit message
while writing up the VPN finding, one of them a public address tied to Mark's own VPN session. The repo is
public. They were scrubbed and the commit amended before anything was pushed. The finding reads the same
without them.

## 2026-09-20 midday, session AB continued: 0.8.0 landed

**Mark's word, in full:** "do all, one at a time starting with merge". The four things the checkpoint had
listed as his: merge, restart, his live sweep, tag and push.

**Merge and restart.** The fast-forward was clean and the suites ran again from the main checkout, which is
the tree the live service actually runs from. Between those two moments the dashboard was already serving
the new page against the old service, which is the hazard that made the merge his call rather than an
agent's; the page degraded exactly as designed, printing nothing where the log line goes, because the old
health payload has no log block. The service was then stopped by the pid in its own data directory and
restarted from the Startup launcher, and came back on 0.8.0 with the watchdog on, the plug armed, and the
log line reporting 9.1 MB against the 25 MB cap. Nothing rotated, and nothing will for weeks.

**The live sweep, at last, and it behaved.** It named the subnet and the address count before asking
anything, said that a service was running, skipped the configured miner and found no other unit. That
leaves the gate's own done-when half met, because the plan asks for the sweep to list the SC-BOX and the
default path deliberately does not probe it. Rather than stop the live service for a clean sweep, one
probe was sent to the configured address alone, timed into the gap right after a sample landed, which is
the same single-request treatment session Z used to establish that the status path answers without a
token. It listed the unit with its model, firmware and hardware, and the next poll was clean: no error, no
gap in the log.

**Running the release found one thing no test had.** The line the security pass added, the one that gives
an operator their single chance to stop a sweep of the wrong network, repeated itself on the single-target
path: "sweeping 1 given address, 1 address, one request each". Fixed, with a test for each of the two
shapes. It is a small thing and it is also the fourth time this gate that running the code found something
the suite could not, which is the argument for the habit rather than for the fix.

**Tagged and pushed.** v0.8.0, annotated, with the release notes naming both gates, the trade rotation
makes, and the two security findings that mattered. The branch and its worktree were left in place for
Mark to clear.

## 2026-09-20 afternoon, session AB continued: "does it truly follow the default gateway?", and 0.8.1

**Mark's question, verbatim:** "verify how the network discovery works. Does it truly follow the default
gateway?" He was checking a claim the session had made in passing, and the claim was loose.

**The answer was no, and the checking mattered.** The code never read a gateway and never parsed a routing
table. It opened a UDP socket towards a reserved address, asked the socket which local address it had been
bound to, and took that. That is a route lookup for one destination, and the answer lands on the default
route only because nothing more specific matches. Verified from both ends: the operating system's own route
query named the same tunnel interface for that destination, and the same call towards an address on the
house LAN named the Ethernet instead, which is what proves it is per-destination rather than a gateway
read. The "no packet is sent" line in the docstring also held: two thousand connect-and-read cycles moved
the machine's UDP datagram counter no more than an idle window of the same length did.

Three limitations came out of that check, and the third is the one that mattered. The prefix was assumed to
be a /24 and never read. The fallback path resolved the machine's own hostname, which is name resolution
rather than routing. And the morning's security fix, which refused address ranges that are not private, was
the right fix for the VPN in front of it and the wrong test in general.

**Mark's instruction, verbatim:** "make the subnet size checked via the router's data! DOn't assume
anything, and write hard rules to NEVER use the VPN to determine address ranges. LAN is LAN. No Further
reach." Then, while the work was running: "Put a special rule that 169.254 prefix addresses are NEVER used.
By definition those are not connected, so don't even consider them. Think about any other 'smart, network
first principles rules' should be applied to not do stupid things when I've lost LAN or Internet
connectivity, or if there are other exceptional conditions."

**He was right about the hole, and it was a real one.** A corporate or WireGuard VPN hands out private
addresses. They pass every "is it private" test and are exactly as wrong to sweep as a public range. The
test had to become what the interface *is*, not what it carries.

**What was built.** A new module whose only job is to say what this machine's network is, reading the
operating system's interface table: the address, the prefix the router handed out over DHCP, the interface
type, the medium, the driver and the link state. Nine rules, each with a test, and the rules were written
into the repository's own agent instructions so the next session meets them before the code. A tunnel is
never a source of a range. The prefix is read, never assumed. The routing table is not consulted, and a
test reads the module's own source to keep it that way. Link-local addresses are never a network, in either
direction: not chosen, and refused even when asked for by name. A disconnected link is not a network, since
a lease outlives its cable and sits in the table looking healthy. A LAN that overlaps a tunnel's own range
is refused rather than preferred. Two candidate LANs are a question. Nothing resolves a name or needs the
internet. And a sweep where every address fails at once reports a dead network with its own exit code,
rather than reporting no miner found and sending the owner to look at a miner that is fine.

**The judgement call worth recording.** Container and subsystem bridges carry private addresses on Ethernet
media and would have passed every rule, so they are excluded by name. The line was drawn deliberately on
the other side of a bridged network interface: that is the real network on a machine whose owner bridged
their card, and excluding it would have broken the setup of the person most likely to have one.

**Two ideas considered and not built,** both additions rather than rules: seeding a sweep from the
neighbour table as a fast first pass, which only knows hosts that have spoken recently and needs a parser
per platform; and testing the gateway before sweeping, which invents a failure mode on any network that
drops that kind of probe.

**Verified on the machine, then released.** The sweep now names the interface its range came from before it
asks anything. The interface query was checked against both editions of the Windows shell, since the newer
one is not present on a default install. Suites 573 and 68, green. Tagged 0.8.1 and pushed at Mark's word.
The running service was left on the previous version: nothing in the poller or the server changed, and only
the discover command behaves differently.

## 2026-09-20 evening, session AC: "seems down-ish", two incidents, and a log that erased itself

**The goal in Mark's words:** "open the goldshell project. Then scan the miner and see what's happening.
Seems down-ish." No code was in scope. It became the first time the tool was used to diagnose a live
fault while it was happening, and it showed what the tool does not keep.

**How the scan was done.** Entirely from the service's own files and its health endpoint at first, so the
diagnosis added no traffic to a miner that was already struggling and did not collide with the watchdog
mid-ladder. The agent held off on any manual restart or plug action for the same reason, and said so. The
miner's own log was read only when Mark asked for it, as one request timed into the gap between two of the
service's polls, with long tokens masked and the copy deleted after reading.

**Incident one, 17:13.** Forty-three hours of steady hashing, then the mining process on the box restarted
with nothing in its log beforehand: no error, no overheat, no pool trouble, just 37 seconds of silence and
a start line. For five minutes it could not write to the first chip in the chain. The watchdog's first soft
restart made it worse (the controller came back seeing no board at all, 3 W at the plug) and its second
fixed it. Sixteen minutes down, no human action.

**A prediction the agent got wrong, and the correction.** It told Mark the next rung would be a plug cycle.
It was a second soft restart. The watchdog only considers the plug when the miner is unreachable, and this
miner answered throughout; that has been the design since 0.7.4. The agent flagged its own claim as
unverified, then read the code and withdrew it.

**Incident two, 18:45, and Mark's hypothesis.** Mark noticed that after the recovery the plug read 162 W
where it had read 184 W all day, and that after a second burst of board resets it was back at 184. He
asked whether one or two chips had been absent. The per-chip nonce counts in the service log answered that
directly: all sixteen produced at their normal rates in the low window (9,035 good nonces an hour against
9,154 before and 9,151 after). Same work for 22 W less, and each step back up passed through a single
221 W sample. That points at the supply or regulator voltage, which is a hypothesis and recorded as one.

**The finding that changed the design.** When the miner's log was read a second time to look at 18:45, it
had truncated itself: 3.8 MB two hours earlier, 36 KB now, starting after the window of interest. The
evidence was gone. An earlier guess that the truncation had caused the 17:13 restart died at the same
moment, since this truncation came with no restart. Two gaps followed from the evening: the service parses
the board's voltage on every poll and drops it, and it fetches the whole miner log every five minutes and
keeps only the temperatures.

**Proposed, not built.** A 0.9.0 (a new log column is a format change, so a minor bump by this repo's own rule) that logs the reported voltage as a column, and that classifies the
non-routine lines of the log read it already makes into labels and counts in a small file. Labels from a
fixed whitelist, never the text, because the miner's log repeats the pool user and a mask is a blacklist
that fails open. The agent named its least certain assumption: the voltage field may echo the setpoint
rather than measure anything, in which case the column can show the firmware changing its mind but not the
hardware sagging.

**Considered and declined:** a new watchdog rung for a miner that answers but has lost its board. The
existing ladder fixed the one case seen, and one case is not grounds for a rung that cuts power.

**Left as it was:** the miner, the plug, the service and the code. The hardware suspicion (power brick or
board connector, since the failures were at the first chip and not the known marginal one) is Mark's to
check by hand.

**Mark's follow-up, and what the history said.** "Do you see any change in rules that are suggested by the
behavior? We're still getting good uptime." Rather than answer from one evening, the agent counted both of
the day's signatures across the fifteen days of log. Twelve reset bursts, every one healed by itself inside
two and a half minutes with the watchdog rightly silent; six episodes of a controller answering with no
board behind it, none of which ever healed until the stall rule's restart arrived, six to twenty-two
minutes later. So the answer was one rule, not several: leave the five-minute stall window alone, because a
quicker trigger would have interrupted a self-healing miner twelve times out of twelve, and give the
board-absent signature its own two-minute soft restart, because waiting there buys nothing. Worth about
0.15 percent of uptime, which the agent said plainly is small. Added to the proposal as a candidate, to be
built only on Mark's word, and kept a soft restart rather than a power cut.

## 2026-09-20 late evening, session AC continued: 0.9.0 built on a branch, from the evening's own evidence

**Mark's answers set the scope.** A separate file for the miner-log summary; build the board-absent rule,
after the other two; and on the deliberate soft restart the plan wanted as a final check: "I'm ok with 1
soft restart, but before you do it, I want data on what % of times a soft restart appeared to cause
additional issues within the next 30 minutes (maybe not causation, but maybe more than coincidence?)". He
also asked what the security pass would cost and whether it belonged in another session.

**The data he asked for changed the plan.** All 17 soft restarts the miner actually received in 15 days,
classified on the following half hour: 6 clean, 6 recovered and then had another issue, 5 got worse first,
against a 0.7 percent baseline for healthy half hours. The agent said plainly that most of that gap is
selection, since a restart is only sent to a miner already in trouble, and picked out the subset that
speaks to cause: of 4 restarts sent while the board was still present, 3 lost the board. Small, but it
matches the cold-start failure this unit already had on record. So the recommendation was not to restart a
healthy miner to test a parser, and the release's done-when was written so that it does not need one. The
same numbers shaped the rule: only a board that is already absent gets the shorter window, because that is
the one class a restart never left worse off.

**On the security pass:** small (one new untrusted input, one new reason for an action the watchdog could
already take) and in a fresh session, which the working rules require anyway: the session that built it
does not verify it.

**What was built, test-first, one commit per task.** The voltage column, stored exactly as the firmware
reports it because the SC-BOX's unit is undocumented. A classifier that turns the miner log's non-routine
lines into labels from a fixed table, and a poller change that writes them to a small file from the read
the service already makes, so the miner sees no new request. The board-absent rule, with the model profile
deciding where it may fire: the SC-BOX alone, because resemblance to it is not evidence. A sibling model the
table otherwise treats as identical was deliberately left out.

**Found by building.** The classifier needed its own read cursor: with the board absent the miner writes
failures and no temperatures, so the existing cursor never moves and every read would have re-counted the
incident. The repository refused the first fixture's name, because an ignore rule exists to stop a real
miner log being committed; the fixture was renamed to the existing convention and the guard left alone.
The older real capture in the fixtures showed the start banner's pool line, the very line that carries
the user, arriving as an unknown shape, which is the case the label design exists for. And the model table
is mirrored in the page's script, with a test that keeps the two equal, so the new profile key had to land
in both.

**A correction the agent made to its own draft.** The proposal first called this a patch release. The
repository's own rules say a log format change is a minor bump, so it is 0.9.0. An early draft also said
five of six board-absent episodes followed a power cycle; the event log showed two.

**Left for a fresh session:** re-verify, the one security pass, merge, tag, restart the service at Mark's
word, and confirm a voltage value and a first label row on live data. Not merged, not pushed, service
untouched.

## 2026-09-21 just after midnight, session AD: the fresh-session verification, one security pass, and 0.9.0 live

**The goal, in Mark's words:** "pick up the Goldshell project and carry forward with the specified security
testing after any small outstanding changes." The session worked from the written brief the build session
left, restated it, and found no outstanding changes: the branch was exactly as the checkpoint described.

**Verification in a session that did not build it.** Every test module was re-run one at a time with the
failure names kept this time, since the build session's script had lost one. The counts matched the build
session's evidence: 627 Python and 68 Node, all green.

**One security pass, as planned.** A single reviewer, briefed with five questions and a tool budget, read
the diff and probed the classifier with hostile input. Nothing critical or high. The miner's log text
cannot reach the file, the event log, a terminal or the API; no pattern backtracks (the worst case, 4 MB of
tiny lines, took about a second); the new restart rule stays under the daily cap and can never reach the
power plug. The agent checked each finding against the code before acting on it.

**What it did find, and what was fixed.** The timestamps are the one place the miner's own characters are
written, and they were looser than the design claimed: `\d` in a Python pattern matches any Unicode digit,
and nothing checked the date was real. Worse for the evidence the release exists to keep, one line stamped
in the future would become the reading cursor, and reads would then alternate between skipping everything
and counting the same lines again. The reviewer proposed validating each timestamp; the agent added one
thing, skipping any line stamped after the log's own last line, because a real clock glitch into a future
year is a valid date and validation alone would not stop the loop. Also fixed: a full `minerlog.csv` stayed
unwritten until a restart although its message says moving it aside starts a new one, and the config
accepted NaN and Infinity for the new setting. Each fix came with a test that failed first. 632 Python and
68 Node green afterwards.

**Left for later, at Mark's word.** The log read itself has no size cap. It predates this release, and a
plain cap would keep the oldest part of the log and drop the newest, which is the part everything reads,
so it needs a design rather than a fix. Recorded in `docs/security-notes.md` as open.

**Released.** Fast-forward merge, tag `v0.9.0`, pushed at Mark's word. The live service was restarted onto
it at his word, by the pid in its own pid file, after the log was copied aside by hand (an old backup
already existed, so the migration would not make one). Confirmed: health reports 0.9.0 with samples
advancing and the two-minute board-absent rule on; the log gained its voltage column with no ragged rows,
and new rows carry a value. The miner itself was not restarted. The first label row will appear the next
time the miner re-initialises its board on its own; nothing was done to provoke one.

## 2026-09-21 midday, session AE: a lost session that wasn't, a 162 W mystery, and 0.9.1

**The ask.** "Something was in process in a session from last night ... but the session was lost this morning due
to a crash or a power outage. Check if anything was left un-done or un-written." Then: "Look into the 162W
operation first ... I'll take it, but see if you can find some clues. Then do the 1) list of activities as
recommended. We will expect to merge after the bug fix, and I pre-authorize a service restart."

**Nothing was lost.** The late session's transcript ends on its own final summary after the release, the
checkpoint and the memory update; the repo was clean and level with GitHub. The outage came seven hours later:
logging stops at 07:27, the PC booted at 09:29, and the service came back by itself from its Startup entry. The
miner went down too (2 W, the web backend answering 500) and two watchdog soft restarts brought it back.

**The standing power-cycle check.** At 04:22 the miner stopped answering, both soft restarts timed out, the plug
cycled it at 04:31 and it hashed a minute later. A burst of addressing and self-test failures at 04:37 healed on
its own within a minute. The item left open last night closed: `minerlog.csv` now exists and holds no pool user.

**The 162 W state (Mark's question).** A scanner over the whole log found seven stretches of at least 15 minutes at
about 162 W instead of about 182 W since 09-14, three of them today, one still running. Same clock, same fan
speeds, hashrate within 2 %, all 16 chips producing at matched rates (per-chip ratio 0.95 to 1.06), and boards 3
to 4 C cooler. That last point argues against a metering artifact: less heat on the board means less power really
went into it. The starts are spread across the day, and one began with no event at all, so it is not a restart
side effect; a power cycle did not end one either. About 234 J/TH against 257. The best-supported reading is a
lower supply voltage at the same work, but nothing measures it: the firmware's `volts` figure stayed 0.41 in
both states, and whether it is a measurement at all is still open. Mark takes the efficiency; no change made.

**Three bugs one outage exposed, fixed as 0.9.1.** (1) The miner's clock restarts at 2007 after a cold boot until
it reaches a time server, while its log keeps the run before. Compared by timestamp, the whole previous run then
looked newer than the cursor and `minerlog.csv` counted it a second time. (2) The same comparison put the previous
run's 79 C peak on a row one second into the miner's uptime. Both now read the log by position: the cursor is
found where it sits, and temperatures keep only what follows the newest run start. (3) The service started while
port 4028 was still closed and settled on the fallback source for the whole run, which carries no voltage, so
`volts` went blank from 09:44. It now decides only once the fallback actually answers. Each fix had a test that
failed first; 640 Python and 68 Node green.

**One review pass.** Nothing critical or high. Its low finding, that a pool user containing a run-start phrase
could move the run start, was fixed (the pattern is now anchored). A medium one was deferred and recorded in
`docs/security-notes.md`: a miner that never gets network time repeats the same 2007 stamps every boot, so a
cursor can match the wrong boot. The old comparison also lost lines in that case, and a fix needs a design.

**Release, recorded after the fact.** This entry was committed before the push. At Mark's word, 0.9.1 was pushed to
GitHub at 13:50 on 2026-09-21 (main `67f303e`, annotated tag `v0.9.1`), and the live service had already been
restarted onto it at 13:38 with his prior authorization.

## 2026-09-21 evening, session AF: designing for outages, no code

Mark asked to design the deferred finding that a miner which never gets network time repeats its 2007 stamps each
boot. Then he widened it: "We must build in handling for any such 'no NTP feed' for some time (a few hours) or
'forever'", and power here is erratic. Storms take the grid and the ISP together, a Powerwall carries the house, and
when it runs low "I'm just gonna run around the house and start pulling plugs". Nobody presses Hold on the page in
that moment, and hosted-site operators don't shut down gracefully either.

**The rule adopted.** gbox acts only on evidence that points at the miner itself. When an observation could mean
the path, the power or the pool is down, it logs once and waits, and spends nothing from its daily caps. Mark on
why gbox has no AI in it: it can count on neither internet nor a local model, and "simple sequential logic and
timers is perfect for an unforgiving environment".

**What gaming the sequences found**, from reading the code, none seen in the field. The stall rule would soft-restart
a healthy miner every ten minutes through an ISP outage, and earlier data says such restarts often lose the board.
A cycle never reads the plug's meter, so a miner hashing behind a dead switch, or one whose cord was pulled, still
gets cycled. The worst one is about recovery: failed restart attempts during a LAN outage count against the daily
cap, so an outage of about two hours spends the budget that this morning's bring-up needed. A PC that dies mid-cycle
leaves the relay off, and gbox then reads that as "off on purpose". The plug's button toggles, which races a cycle.
Two limits came from the repo's own rules: no default-gateway probe (the routing table is off limits), and no
`pools` command (it carries the pool user).

**Decisions (Mark).** Split into two gates: 0.10.0 (log read by position, a pool-down hold on the stall rule, a
meter veto on cycles, and "LAN dark" meaning nothing is sent or counted), then 0.10.1 (interrupted-cycle detection,
relay re-read, miner identity). One restart after 8 hours of pool outage "just in case". 10 W counts as unpowered.
An interrupted cycle is a config option, defaulting to leave the relay off with a loud notice. Mark's devices
already have DHCP reservations, so the identity check is for other owners. The plug has no power-on setting;
whether it restores its last state after an outage is a hand test for later.
Brief for a fresh build session written outside the repo.

## 2026-09-21 night, session AG: the other developer's repo, a voltage that isn't one, and an SC Lite on record

**Goal.** Mark: "read this repo and learn more about the Goldshell SC Lite", pointing at the other developer's
goldshell-config. The developer owns an SC Lite and wanted what gbox knows about the SC5 Pro and Pro II; Mark
wanted the SC Lite verified in gbox and a theory checked: that the SC-BOX's 0.41 `volts` is the plan's setpoint,
not a reading.

**What was already known.** gbox had read the same repo on 2026-09-12 and built its SC Lite row and the `mv_pv`
dialect from it, so the session's first "new finding" (the HS Box plan writes `0.41 V`) was already in
`firmware-api.md` as an open question. New since then in that repo: pool failback, a fleet page, a probe for
unknown boxes, a 4028 short-read fix (gbox reads until close, so it never had that bug).

**The volts check, done without touching the miner.** A read of `/mcb/setting` could not settle it (the plan has
always said 0.41), so the day's own log answered instead: all 1778 rows read exactly 0.41 through 162, 184 and
221 W stretches and through samples under 50 W. The SC-BOX firmware reports no measured supply voltage at all.
Not yet checked during a board-absent episode.

**Pushback, and a restart that was not caused by a read.** The fans went to full at 21:55 and Mark suspected the
session's read, and his page refresh earlier. The session had sent nothing to the miner. The log showed a miner
restart (uptime to 13 s, reboot counter to 0), and every fan burst that day was a restart or a boot after a power
cut. Page loads only read; its three writes need a button and a confirm. Hang onsets were spread evenly against
the 5-minute syslog read. With Mark's approval, one timed read of each log (`/dbg/minersyslog`, then `/dbg/syslog`,
both between service polls, copies deleted after): the evening's four hangs sit among hundreds of "Auto addressing
failed" / "REINIT THIS CPB" lines, the controller losing its hashboard; the 18:36 and 21:55 restarts were the
supervisor restarting itself with no request logged and no power loss. The backend does not log `/dbg/` reads, so
the page is not cleared: Mark is closing every dashboard tab overnight as the test. Found on the way: the miner
posts its serial, MAC and LAN address to find.goldshell.com about hourly. Mark wants it blocked at his resolver.

**The capture.** Mark sent the developer the capture request; he published a full SC Lite capture. gbox's parsers
read every file unchanged. The session disagreed with two of his conclusions: that only the raw `Authorization`
form worked (only that form had been tried) and that `tempcontrol` drives the fan loop (a log taken with it on
cannot show that). He then ran both: Bearer works, and 60 s with `tempcontrol` off did not stop the loop. His
fan log also showed lone -150 board readings on a hashing board, which is why `absent_signature` stays off
until a model's own log earns it. The model row now reads 4 boards, 4 fans, `/dbg/` open, verified. Commit
`10f9d79` on branch `sclite-live`, 646 Python + 68 Node green; not merged, not pushed.

**Credit and a longer test.** Mark: credit him as MaVeTh; he says most of his work is MIT, perhaps not this
repo, and Mark chose to publish on that. MaVeTh then ran `tempcontrol` off for 10 minutes with a 75 C abort
(peak 70 C): the fan loop kept walking toward 85 C the whole time. What the flag does under heat is still open.

**Left open.** The `volts` label on the page still reads as a measurement; a relabel is 0.10.x work.

## 2026-09-22 afternoon, session AH (65231045): the tabs-closed night, how the page talks to the miner, and an unplugged internet

**Goal.** Mark: "pick up the Goldshell project. There are 2 sets of morning actions, left from 2 parallel
sessions" (AF and AG). Then: explain how the dashboard and the service interact, note it for the User Guide,
and run the pool-outage timing test with him pulling the switch's uplink.

**The tabs-closed night answered AG's question.** With every dashboard tab closed (Mark's word), the miner
still hung at 00:07 (port 4028 errors, restarts timed out, plug cycle at 00:16, hashing 90 s later) and
restarted itself silently at 06:58 (uptime reset, share counter from 840 to 11, `process_started` in the
miner log, no watchdog event). Page reads are not the cause. The standing power-cycle check found four
cycles since AF, each ending its settle gap early, none restarting a healthy booting miner.

**The minerlog.csv check (AE's ask) passed.** No duplicate rows across the 00:17 cold boot (0.9.1's
cursor-by-position held through a 2007 clock), and no pool user, URL or credential in the file. The
162 W state is still on (~167 W all night).

**How the page stays current.** Read from `app.js`, not measured: an open tab reads the miner directly
from the browser every 10 s (`dbg/minerinfo`, `dbg/icinfo`, and three slower endpoints once a minute),
and reads the service every 60 s for its status line and the history charts. So each tab is its own client
of the miner, and the one-request-at-a-time rule holds only inside a tab. Mark had two tabs open the
nights before: three uncoordinated clients. Mark asked for a "How it works inside" section, with
diagrams, in the User Guide to be written before the full release; it is in `docs/plan.md`.

**The outage test.** The agent could not stop the live service (Claude Code's auto-mode permission check
refused it, pid file and Mark's go notwithstanding), so Mark stopped it himself with a one-liner that reads
`gbox.pid`, then ran the read-only timing script. On the agent's own "unverified risk" note it read the
miner log once a minute instead of every 30 s. Asked whether the agent would keep working with the
internet down: no, the model runs remotely; the script runs alone on the LAN. Mark's keypresses each lagged
the cable by 30-40 s, which he reported, and the times below are corrected for it:

- `stratum_interrupted` about 2 minutes after the cable came out, `pool_not_responding` about 2.5.
- The hashrate figure froze at once. **About 8 minutes in, port 4028 went dead and the miner log went
  silent while the web API kept answering**, for 16 minutes, until the cable went back.
- About 15 s after reconnection the mining process restarted by itself and shares began at once.

**The finding that changed the design.** The 0.10.0 brief planned an upstream hold on the *stall* rule.
The test shows an ISP outage also reaches the *unreachable* rule: once port 4028 dies, the service's
board read fails exactly as in a hung miner, and today's code would soft-restart and then power-cycle a
miner that is only waiting for its pool (from code reading; the service was stopped for the test). The
signature that separates the two: in an outage the web API still answers and the newest log lines are pool
labels; in the real hangs the restart request timed out too. Added to the gate 1 brief.

**Left open.** Two "Accepted" lines the miner logged 8 minutes into the outage, unexplained. The service
was restarted from its Startup launcher and confirmed polling.

## 2026-09-22 late afternoon, session AI (3b2a6d0a): 0.10.0 gate 1 built, outage-proof watchdog

**The goal**, from the gate 1 brief written in session AF: "a proposal that's robust for things going
down unpredictably and coming back up in weird orders", because "the last thing I'm going to think about
in that situation is accessing the Gbox page and pressing Shut Down or Hold." The design rule: act only on
evidence that points at the miner itself; when an observation could mean the path, the power or the pool,
log once, wait, and spend nothing from the daily caps.

**Restated before code, and two corrections to the brief.** The agent restated goal, constraints and
done-when and Mark answered "go in that order" (A, then B, then C and E). The brief named a stale `main`
commit (AH had pushed since); the agent branched from the real one. It named its least-sure assumption up
front: B's signature for an ISP outage ("web API answering, newest log lines are pool lines") rests on one
measured outage.

**Tests first, each run against the old code.** Every part was written as scenarios before the fix, and
run on the unchanged code so each failure was the defect and not a missing function: 5 of 10 log-cursor
scenarios failed on 0.9.1, 4 of 6 pool scenarios restarted the miner, both "can't see" scenarios spent 6
restart attempts in 30 minutes. The ones that already passed stay as guards. Raw output is kept outside
the repo, in the gate's evidence folder.

**A. The log cursor is a place, not a time.** The cursor is now the index of the last line read, the
line count, and a hash of that line; the line's own text is never held, since it can carry the pool user.
The brief said a first read stops at the "run start"; the agent changed that to the process start only,
because the miner re-inits its board mid-run after a fault (4 inits against 2 starts in the incident
fixture) and the lines before a re-init are the ones that explain it. A line stamped in the future no
longer needs special handling: it cannot hide later lines when the cursor is not a time.

**B. The pool.** The timing risk the brief flagged was real: the log is read every 5 minutes and the pool
lines appear 2 to 2.5 minutes into an outage, so a stall verdict at 5 minutes could arrive before the
evidence. Rather than lengthen the stall window, a verdict now asks for one fresh read and waits at most
three polls for it. When port 4028 is dead the service still reads the log from the web page, and that
read doubles as the "web API answering" test. **A decision from the measurement:** the two unexplained
"Accepted" lines 8 minutes into AH's outage mean a log share line cannot end an episode; only the
accepted counter moving does. After 8 hours of one outage, one soft restart (Mark's decision in AF).

**C and E.** The power rung reads the meter first: a working miner (at or over 100 W) or no load (under
10 W, Mark's default) is not cycled. An unreachable miner is "can't see", nothing sent and nothing
counted, when the plug is silent too, the meter shows working power, or this PC's own LAN link is down;
the link test was added to the network module under its existing rules (no socket, no routing table, no
name lookup). The agent considered adding "under 10 W" to E and did not: that morning the miner had
answered HTTP while the plug read 2 W, so a low reading does not prove the controller has no power.

**Caught in the session.** The first full-suite run had 9 errors: tests that hand the poller a minimal
stand-in watchdog broke on the new hooks. The poller now uses a hook only if the watchdog has it. A test
the agent wrote for a "miner-first" start was wrong (the fixture's share counter never moves, so it tested
a stall) and was removed rather than bent to pass.

**Left open.** One reviewer pass at feature-complete (the policy default), in a fresh session. The residual
risks are written into the security notes: a pool blip read just before a genuine hang holds the watchdog
for up to 8 hours; one failed log read during an unreachable episode lets one unneeded restart through.
Unverified: whether the firmware's log ever ends mid-line, and whether a boot with no pool can look like a
missing hashboard. Nothing merged, pushed or deployed.

## 2026-09-22 evening, session AJ (d5579e54): the reviewer pass on 0.10.0 gate 1, and what it changed

**The goal**, from Mark: "run the reviewer pass on 3a31147..5373f16", and "show me the findings first"
before any fix. The review ran in a separate reviewer agent with fresh context, read-only, forbidden to touch
the miner or the live service.

**What the first review found.** No critical. One high, confirmed by reading the code: the watchdog took
any change of the accepted counter as "the pool is back", and a restart sets that counter to 0. So the one
restart allowed after 8 hours of an outage ended the outage in the watchdog's eyes, wiped the evidence the
restart itself produced, and let the stall rule restart again. The reviewer simulated 3 restarts in 72 h;
the test written for it showed 36, the daily cap every day. The existing test could not see it because its
counter stayed at 500 after the restart. Four mediums: one bump of the counter spent the pool evidence (the
measured outage logged two shares 8 minutes in); a service started after the log went quiet, about 8
minutes into an outage, looked back only 5 minutes and missed the pool lines; the "can't see" link test
assumes the miner is on this computer's own subnet; a failed sample now triggers a full log fetch.

**Decided with Mark.** The agent recommended fixing the high and the first two mediums before merge ("the
outage fix leaking restarts in exactly the situation gate 1 was built for"), checking that an older
config.json still loads, documenting the subnet limit, and deferring the rest to gate 2. Mark: "go with 1-5
as proposed". A fact from the outage measurement settled the design of the fix: the counter froze at 983
for the whole outage and, once the pool returned, restarted at 0 and rose every 10 to 40 seconds. So the
pool now counts as back only when the counter rises on two samples at least a minute apart, and a fall is
never a share. A service start reads 30 minutes of the log for pool evidence, never past the newest
process start, while the rows kept in minerlog.csv keep their 5 minutes. An older config with a low
`boot_watts` would have stopped the service from starting; the new setting's default now yields to it.

**The second review, of the fixes, in another fresh reviewer.** It found the wider look-back also ran
after the miner truncates its own log, replaying pool lines already spent (fixed: a genuine first read
only), and that a decimal `boot_watts` still failed (fixed). Its main point was a trade-off, not a bug:
a service restarted within 30 minutes of a pool blip that had already cleared reads the blip again and can
hold a genuinely hung miner for up to 8 hours. The agent recommended documenting it as a residual rather
than adding log-timing logic for a rare case; Mark chose that.

**Caught in the session.** A scripted insert rewrote every line ending of a test file (553/527 in
`git diff --numstat`); the file was restored and the test re-added with an edit. The first version of the
look-back fix widened it to the whole log whenever the new setting was absent; a parser test caught it.

**The subnet question became a design direction.** Asked where the service would run once the home network
has VLANs, Mark chose a hardened host inside the mining VLAN, possibly also a blockchain node: the
same-subnet case, so today's check needs no change. The agent pushed back on two points that follow from
it, and Mark added a third. They are open for a later session: the dashboard listens on the local machine
only and has no login, so reaching it from the Mac needs managed access (an SSH tunnel or authentication);
a node takes internet traffic by port forwarding, which makes it the likeliest way in, next to a miner and
a plug that protect nothing; and even on today's flat LAN, an open dashboard beside an unprotected miner
is a lateral-movement risk if any machine or IoT device is compromised.

**Verified.** Every fix came with a test that failed first. The full suite on the first fixes: 736 Python
tests in 18 modules, one at a time, and 68 Node, green. After the second round, the six affected modules
were re-run green. Nothing merged, pushed or deployed.

**Correction, written in session AK.** That line was true when written and stale an hour later: at Mark's
word ("merge it, push, tag v0.10.0 and restart the service") session AJ merged, pushed and tagged 0.10.0
and restarted the live service on it the same evening.

## 2026-09-22 night, session AK (79079c12): 0.10.1, the dashboard answers only to its own name

**The goal.** The brief from AJ: "Close two dashboard security holes and one watchdog residual found after
the 0.10.0 release, as 0.10.1." Both holes predate 0.10: no Host-header check, so DNS rebinding worked
(verified in AJ: a forged `Host: attacker.example:8765` got 200 from the live service); and
`/api/hold/release` took an empty POST with no JSON check. The residual is the service-restart variant of
the 8 h hold.

**The question that mattered.** The brief said to accept "the bind address". The agent asked what that
meant for a wildcard bind, since no browser ever sends `0.0.0.0`. Mark supplied the AJ discussion: the
bind address means `--bind` to the box's own LAN address, so the Mac can reach the dashboard; the other
route is an SSH tunnel, which arrives as localhost. The wildcard case never came up. The agent proposed
treating it as loopback-only with a warning, and dropped its own first idea of an `--allow-host` flag as
a feature, not a patch fix. Mark agreed. The agent also said plainly that a Host check stops rebinding
only, not a LAN device that can reach a `--bind` port directly, and put that in the security notes.

**What was built.** A request must carry exactly one Host header naming 127.0.0.1, localhost, [::1] or
the `--bind` address with the port, matched whole, or it gets 403 before any route runs. Hold release
needs JSON like every other write; app.js already sent `{}`. On a service's first read of the miner log, a
pool line is spent when share lines after it span a minute of log time. A test the agent wrote for a clock
set back to 2007 and forward again showed that looks like years of shares, so a step between shares that
goes back, or forward by more than 5 minutes, restarts the span. The measured outage (two shares 30 s
apart, then silence) is still held.

**The review found a hole older than both fixes.** The `reviewer` agent, in a fresh context, showed that
a refused POST (415, 413, 404) was answered without reading its body and the connection kept open, so the
body was parsed as the next request. A no-cors `text/plain` POST from any site, which needs no preflight,
could carry a complete JSON write: a hold with no expiry, the plug On, a junk token. It reproduced this
against a scratch server. 0.10.0 had the same flaw, and the agent's new security-notes text had claimed
cross-site writes were closed. Fixed: the connection closes after any request whose body was not read
whole, and chunked bodies are refused. A second pass by the same reviewer tried 17 variants and found
nothing a browser can reach. One low was left for later: duplicate Content-Length headers, which only a
raw client can send, and a raw client can already send any request directly.
An Origin check was considered and left out; with the connection fix it only guards a future route.

**The fix had a side effect, found by running the tests five times.** In the full run `test_server` had
one unexplained error. The agent stopped under the brief's three-fixes stop-loss on one file and asked.
Mark chose a test-only hardening and five runs. The traceback then showed the cause: on Windows, closing a
connection with its body unread sends a reset, which can destroy the 413 or 415 reply before the client
reads it. It hit the new tests first, then two tests from 0.10.0 that check exact status codes. The agent
said its own recommendation (tolerate the reset in the tests) had been wrong, because it would stop those
tests checking the API. Mark chose the server fix: read and drop a refused body up to 64 KB before closing,
and a 30-second socket timeout, which also closed the reviewer's "no read timeout" low.

A third reviewer pass found nothing above low. It corrected one doc claim: a stalled body costs 30 s, not
60, and the timeout is a gap between bytes, so a raw client sending a byte at a time is still not
bounded. It also raised an unverified risk that the timeout could cut off a large log download on a slow
direct `--bind` link. Both are written into the security notes.

**Verified.** Every fix came with a test that failed first. On the final code: `test_server` five times in
a row, 120 tests each, all green, some runs under the reviewer's parallel load (the load that had
produced the resets); `test_cli`, the other module that imports the server, green. The other 16 modules
and Node were green on `aec8c9e`, and nothing they import changed since. Nothing merged, pushed or
deployed.

**Released, same session, 2026-09-23.** At Mark's word main was fast-forwarded, pushed and tagged
`v0.10.1`, and the live service restarted on it. Checked against the live service: a forged Host got 403
where 0.10.0 gave 200, and a blind text/plain hold release got 415. The first browser check failed for a
reason outside the code: the Chrome tools were connected only to the Mac's browser, so 127.0.0.1 was the
Mac. Once Mark connected this PC's Chrome, the dashboard loaded with every API call answered and the
page's own token hand-off accepted.

## 2026-09-23 afternoon, session AL (ad4a18b6): the Mac as the full console, designed

**The goal.** "Push it, then start the Mac access design." Asked what the Mac needed to do, Mark chose
everything, password buttons included, LAN only: "That dedicated management machine might not be physically
as accessible, so I want the Mac to be able to be the full remote management console for me."

**Scope moved three times, each by a fact.** Mark plans to move the service off the Windows PC to a
dedicated Linux box; his wall-meter readings (65 W and 37 W for two old laptops) against a ~2-3 W Raspberry
Pi settled that a 2013 Pi goes first. A second miner (an HS-BOX, Handshake or Blake2b-SC) arrives tomorrow,
so the controls redesign must be drawn for two miners. And Mark's wish for a phone glance at the stock
Goldshell page led to the question of how to administer an isolated miner later; the agent's answer was
three separate needs: push alerts for "is it hashing", a read-only glance page only if alerts leave a gap,
and pool or payout changes through the gbox box as a jump host. It flagged the payout address as the
highest-value target on the setup, since changing it breaks nothing visible, and proposed pinning it.
Mark set the order: console access, multi-miner design, controls redesign, then alerts with the pin.

**Approaches.** An SSH tunnel to the unchanged loopback service (chosen); the same plus a forward to the
miner's own web UI (later, once the miner is isolated, and only if the stock UI survives being opened as
localhost); a login inside gbox with a LAN bind (rejected by both: new authentication code in the service
that holds the plug, plain HTTP, a password form in place of an SSH key). The gbox key is tunnel-only; a
shell key for general administration is a separate line.

**The finding that shaped the build.** Reading the page showed every open dashboard polls the miner from
its own browser every 10 s. A Mac tab beside the PC tab doubles the load on firmware that crashes under
bursts, and the two browsers would overwrite each other's miner token at the service. Mark chose a lease:
one page per service polls; the newest page load wins; a returning page does not take a held lease, so it
does not jump between machines as each wakes. Mark's overnight habit (Mac asleep, lid shut) added two rules:
a hidden page stops polling and gives up the lease, and a launchd agent keeps the tunnel alive across sleep.

**Plugs.** The agent showed from `plug.py` and python-kasa's device list that the KP125M plugs Mark had
ordered speak only the encrypted protocol, which needs the TP-Link cloud login on the gbox box; Mark
cancelled them. A Tapo strip failed the same test. Shelly passes technically but failed Mark's reliability
bar on reviews; a research agent could not read Amazon ratings at all and said so rather than guess. Miner 2
gets the meterless HS105 that already drove the full restart ladder on hardware.

**Corrections, both the agent's.** It told Mark AES is not in the standard library; `gbox/aes.py` already
implements it, so the plug decision rests on the cloud login alone. It described the systemd unit as new
work; `scripts/install-linux.sh` already writes one.

**Built.** No code. Two dashboard bugs logged by Mark and queued for the controls redesign: the hold reason
stays in its box after the hold ends (fix: mirror the service's reason read-only while held, clear after);
and confirmation dialogs get "Sent" and "Confirmed" timestamps. The design spec was committed. A handoff and
a small visibility-probe page went to the transfer drive for a Mac session to run two sleep experiments.

**Deferred.** Mark reviews the spec tomorrow, then the implementation plan. The real-miner token test (two
browsers logged in at once) runs tomorrow with Mark present, before the build. Security review once, at
feature-complete.

## 2026-09-25 evening, session AM (dc599025): Miner 2 arrives, and the HS-BOX earns a table row

**The goal.** "scan my network for a new goldshell HS box. iT's running now", then, once it was found and
mining, "ensure you've added it to the device table".

**Found.** `gbox discover` found it on its first real use for a second miner: model string
`Goldshell-HSBox`, firmware 2.2.6, and it skipped the configured SC-BOX so the live watchdog was never
competed with. Mark gave the box and its HS105 plug router reservations; the box moved at once on a cable
re-plug, the plug still holds its old lease. Mark declined to bounce the plug's power to move it until the
box has run long enough to judge, and nothing needs the new address yet.

**Sources, and a correction from Mark.** The agent first took rated figures from a retailer listing (470 GH/s
at 130 W on Blake2B-Sia). Mark pointed to Goldshell's own BOX-series table: 540 GH/s at 145 W, 0.27 W/GH.
The help centre refuses automated fetches, so the row cites the page and says Mark read the figures. The
two-algorithm question (Handshake rates differently) is settled by rating the row on Sia, which the unit runs.
Mark's meter read 149 W at the wall on a 1200 W supply at about 12% load; the agent did not claim the
supply's loss, which nobody has measured.

**Tokenless first, then a login with Mark.** Board and fan counts came from port 4028 without a password.
Mark then ran a small read-only capture script in his own terminal (four GETs, no writes, pool and network
fields masked before the file was written) so the password never passed through the session. It settled
the `box` plan dialect, open `/dbg/`, one 16-chip board and a 70-80 C fan-target range. After 48 minutes
the unit averaged 99.6% of rated.

**The finding that changed the design.** The HS-BOX nests its presets per algorithm in `/mcb/setting`; the
clock code reads only the flat list every other model writes. Against this unit it would see no presets, cap
the clock below the unit's own preset, and start a change from the manual plan, which held the Handshake
settings while the unit mined Sia. Recorded in `docs/firmware-api.md` as a do-not-use; the fix belongs to
the controls work. The row itself changes nothing here: an unknown model took the same path.

**Built and verified.** The row in `gbox/models.py` and its mirror in `app.js`, with a test. Every Python
module and the 68 Node tests green, run one module at a time. No version bump: no log, CLI or API change.

**Left out.** The multi-miner service, plug config for Miner 2, and the clock-code fix.
