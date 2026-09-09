# One marginal chip: how this project was built

This is the story of goldshell-box-tools, written from the session log,
the git history, and the docs. It covers five days in September 2026, one
old SC-BOX miner, its owner, and an AI coding agent. The owner is Mark, a
product manager with an engineering past who does not write production code
any more. The agent is Claude, running in Claude Code on a Windows PC on the
same network as the miner. Neither of them set out to write a toolkit.

## Day one: a miner at 30 percent

The SC-BOX had hashed at its rated 900 GH/s for three days. Then, after a
restart, it settled into a weak state: an average of 272 GH/s, the fans
pinned at exactly 1200 RPM, a hardware error rate of 19 percent, and a
habit of going off the network every hour or two. Mark had tried two power
supplies, two firmware versions, a different pool, the temperature slider,
and an external fan. None of it moved the number.

His first prompt was a diagnosis request with rules attached. The agent
could use the System page's restart link, but no more than once in five
minutes. It should think, propose a research plan, and do nothing until he
approved it.

The plan led with a theory: the fans at exactly 1200 were a firmware floor,
not a fan fault. The firmware was running the chips low because of the
error rate, so the question was why the chips were producing bad nonces.
Four candidate causes followed in ranked order, with power delivery at the
top. The agent asked for the one thing the stock UI does not show, the
per-chip data.

What it found, once it had the miner's own logs and the hidden per-chip
endpoint, was narrower than any of the four. The hashboard was in a
re-initialization loop. Every nine seconds the firmware logged "Read Nonce
Failed 10 Times, Reinit Device", reset the whole board, and ramped the chip
clock from 50 MHz back to 725. Each reset cost ten seconds of hashing. A
counter the UI never displays, `rebootcnt`, read 331 after an hour. Of the
16 chips on the board, chip 8 had produced 1460 of the 1972 bad nonces
while returning 34 good ones. The chips were running at 75 to 94 °C; the
UI's 59 °C was the board sensor, which the fan controller steers on.

The agent proposed a trial: set the clock to 600 MHz through the manual
power plan, a field the firmware accepts and the stock UI cannot reach.
Mark approved it, said he would be away for ninety minutes, and set a
budget: no more than 5000 tokens on checking it, and no expensive polling
loop. He wanted some hashrate to continue because it kept his place on a
pool leaderboard.

An hour later the board had reset zero times. The 20-second hashrate had
gone from a sawtooth between 250 and 750 to a steady 660 to 930. Chip 8
was returning about 435 good nonces an hour instead of 34. The chips were
at 69 to 74 °C. The reading was that chip 8 was marginal, not dead: it
failed timing at 725 MHz and worked at 600. Mark left it there and asked
for the watchdog to run overnight.

## The afternoon the tool became a product

Two of Mark's messages that afternoon set the direction for everything
after. The first was a reaction: "I don't see any of that data in the Miner
page GUI! no MHz, no strings, very basic." The gap between what the
firmware knew and what the vendor's page showed was the product. The second
was a request: turn the fan up as high as was safe, leave a one-line script
for clock and fan, and "is there any way to build a better web page with
the data that's already there?"

By evening there was a read-only dashboard. Mark used it on half of a
24-inch monitor and reported that the chart and the Miner box vanished; he
wanted a time axis with 5, 15, and 60 minute ticks; he wanted the fan
percentage shown. The agent fixed the layout at the root cause, verified the
history buffer's sample interval by timing it (two samples in 112 seconds),
and reported a finding along the way: the fan field in the power plan was
dead. The only lever on fan speed was the controller's target temperature
on the board sensor.

Mark then pasted the browser console into the chat. The miner was answering
401 to roughly one request in two hundred. That became a rule the toolkit
still follows: one request in flight at a time, and a 401 retried before it
is believed.

At 20:24 he asked for the thing that made it a project. He wanted a
publishable version in his crypto-focused GitHub account, for other owners
of SC-BOXes and other Goldshell Boxes. He wanted a license recommendation
for cypherpunks, protected buttons so people would not have to run scripts,
an install that "near-normies who are somewhat technical" could manage on
Linux as well as Windows, and a tree of changes. He called it his gift to
Bitcoin mining with Goldshell Box machines.

The proposal that came back was the plan the repo still follows. Secrets
and personal data out of the tree: the password file, the logs, the miner
syslog copies that carry a pool wallet, the saved settings that carry a MAC
address. No hard-coded miner; chip and board counts read from the device,
since other models differ. The PowerShell and openssl shell-out replaced by
about 120 lines of pure-Python AES so there would be no dependencies on any
OS. Three processes folded into one `gbox serve`. MIT, with the reasoning:
Bitcoin Core's license, the shortest, no obligations on forks, and a gift
does not need a patent grant.

Mark added a constraint: he would rather have users type credentials into a
pop-up than store any secret anywhere. The agent agreed and named the cost.
A watchdog with nothing stored sits idle after a reboot until someone types
the password again, which is exactly when it is needed at three in the
morning. The design that shipped keeps the password in one place, the
dashboard's login box; the page hands the session token to the local
service over localhost, in memory only; the command line prompts every
time; and `--remember` is an explicit opt-in whose trade-off the README
states. One honest note went into the docs: on this firmware the token is
deterministic and never expires, so the browser's copy is password-
equivalent, the same as the stock UI's.

His reply was one line: use the suggested repo name, and the page should
work standalone without the service. That second clause shaped the
credential flow and turned up the fact that the firmware's CORS policy is
wide open, which is what makes a standalone page possible at all.

## Rules of engagement

The build sessions that followed all started the same way, with a brief
Mark pasted from the status document: a Goal, the ground-truth files, the
constraints, and a Done-when, ending "Restate the task before acting."
They ended the same way too: "Update the status doc and commit everything
green, then stop," usually followed by "is the watchdog running for
tonight?"

The constraints did not change across five days. Standard library only.
One request in flight. Never faster than one poll per ten seconds. No
secrets in the repo. Build against a fake miner first; the real unit only
with Mark pressing the buttons. State lived in files, not in the session,
so any session could be killed with a quarter hour of work at risk.

Mark's feedback was specific and immediate. The first login modal was
"unacceptably fiddly," with the cursor jumping between fields; six minutes
later, "Good rebuild!" The fan-target dialog's full request body was
"something scary" because it showed the 725 MHz preset; the next morning
the dialog led with a one-line diff per changed field and folded the body
under a toggle. When the agent launched a console window with the wrong
PowerShell, he named the preference once and it went into the machine
notes every agent reads.

## The freeze, and a detour into security

On the second morning the pool reported the miner offline. The agent found
it had dropped off the network at 10:12:34: no HTTP, no ping, no ARP, while
the router answered in two milliseconds. The watchdog had done its job,
diagnosed "unreachable for two minutes", and sent a soft restart three
times. Each timed out, because a frozen controller cannot take HTTP. The
agent gave a three-step checklist ending in a power cycle. Mark power-cycled
it. The manual 600 MHz plan survived, and the gap went into the open items:
a software watchdog cannot recover a frozen controller, and a smart plug on
ping loss could.

That afternoon Mark asked for a security note about disabling the miner's
WiFi. The agent could not scan for the network without changing a Windows
privacy setting, which it declined to do on its own, so it reported the
evidence instead: the firmware object has no access-point mode. Then it
raised something nobody had asked about. The miner held two WiFi networks
and their password in plain text, readable through the API. Four different
write shapes were accepted with HTTP 200 and silently ignored. Mark's
reply: those were not his networks; they belonged to the barn-find unit's
previous owner and had survived a factory reset. He asked for the
enable-then-disable experiment anyway, "so I can share with others dealing
with this flakey box product." The firmware ignored the write in that state
too. It is all in `docs/security-notes.md`, which contains no credentials.

The agent also told him where the session transcript lived, who could read
it, and gave a one-line scrub for the credential that had passed through
the chat.

## The push, and the trap in the stock UI

Mark asked where the GitHub push stood. The agent's answer was "not yet, by
design," with each gate's status and a recommendation to wait until the
buttons existed so the first public commit would do what the README
promised. Mark wanted it public that day, to share with someone whose Box
was dead from a bad temperature sensor. The commits were squashed into one
under a no-reply identity and pushed. Version 0.1.0.

The buttons came the next evening: clock in 25 MHz steps, fan target, soft
restart, and a preset picker, each showing the exact request before sending
it and asking for the password again where the change was dangerous. The
agent reported one deviation from the plan on its own: the preset picker
also asked for the password, because on this unit a preset meant 725 MHz,
the reset-loop clock.

The following morning Mark asked a product question: which settings on the
stock Miner page were safe to Save, and whether the project should offer a
replacement settings page. The agent read the vendor's page source instead
of guessing. The Miner page's settings block had a fan-target slider, and
its Save handler always wrote `manual: false`. Nudging the slider there
silently returned the clock to the factory preset and the reset loop. The
rest of the stock UI wrote to its own endpoints and was safe. A per-page
safety table went into the README. No replacement page was needed.

## The experiment, by hand

That night Mark set the clock to 500 MHz himself, with the new button, "to
see if errors decrease on the bad chip 8." By morning there was a small
script that computed one chip's bad-nonce share per clock segment from the
service's log, with a rule in its docstring: compare the share, not the
count, because a slower clock attempts fewer nonces.

Then the goal changed. "I want to optimize clean nonce rate = max output of
valid shares for mining." Not only stop the resets; find the most
productive clock. Mark stepped the miner to 550 and then 575 MHz through
the day, and asked for a check every two hours. Each check was a table with
the same columns. At 575 MHz the chip showed its first bad nonces since the
clock came down, six of them, 1.69 percent in 39 minutes; the agent said it
could be noise and asked whether to keep watching. By evening 575 MHz had
held for ten hours at 0.10 percent with the same six nonces.

Along the way the agent flagged an anomaly rather than smoothing it: one
600 MHz segment computed at 1521 accepted shares per hour, 2.4 times the
others, cause unknown, treat as suspect. Mark, for his part, moved his
external fan to pull air off the miner's outlet side and noticed the
internal fans slowed more than the temperature dropped. The board sensor
fell 2.4 °C; the fans fell from 1860 to 1200 RPM and stayed there.

## Turning the method into a feature

On the evening of the fourth day Mark took a screenshot of the previous
session's table and asked for it to become part of the app: a table above
the event log showing, for every clock the user has run, the duration, the
worst chip's bad share, bad nonces per hour, board resets, the HW error
rate, and accepted shares per hour. He also wanted written instructions for
running the experiment by hand or automatically, "to find the most
productive and less stressful clock rates and fan settings."

Before writing the plan, the agent ran a throwaway script over the real
log to ground the column definitions, and two things changed the design.
The 1521 per hour anomaly had the same hashrate as the 643 per hour segment
next to it. It was a different pool session, and pools set the share
difficulty per connection, so accepted shares per hour was a poor
throughput number and the table needed a hashrate column. And every
counter reset on a controller restart, so a run had to break where uptime
dropped, not only where the clock changed.

The plan added four columns to the log so the error rate and the fan
setting came from the log rather than the firmware's running average,
migrated the existing log in place with a backup, and split the work into
a table and an unattended runner. It listed six decisions with reasons,
the largest being that the runner would be a command-line process, not a
service thread, so the service would never gain an endpoint that could
change the clock. Mark approved it as written.

The build was test-first, 101 tests by the end. The runner was tried
against a fake miner. A security review of the branch found nothing. The
service was restarted on the new code at 21:28; the first real sample
confirmed that the firmware's Hardware Errors counter equals the chips'
bad-nonce sum, which made the per-run error rate exact from then on.

At 21:46 Mark wrote: "run the runner against the real miner."

The agent asked one question with a recommendation attached: which clocks,
and whether to run it in a visible console so that Ctrl-C would apply the
safe end clock. Mark took the recommendation. 600 MHz for six hours, end at
575.

The guard tripped at 22:23, at the first check after the step was thirty
minutes old. Chip 8 was at 8.5 percent bad share at 600 MHz. The runner
set the clock back to 575 and exited with status 2. Two days of the first
observation, reproduced in 34 minutes by the tool the observation had
produced.

There was one scare. In the first 29 minutes back at 575 MHz the chip
showed 2.9 percent bad, far above the earlier runs. By morning the row read
0.11 percent over twelve hours. Errors lag a clock change by about half an
hour; that went into the guide as a reading tip.

## What the numbers say

| Clock | Chip 8 bad share | Board resets | Hashrate | Wall power |
|---|---|---|---|---|
| 725 MHz, factory preset, healthy | not measured | 0 | about 920 GH/s | 223 W |
| 725 MHz, factory preset, reset loop | 1460 of the board's 1972 bad nonces, 34 good | 332 in the first hour | about 30 percent of rated | 71 to 230 W, bouncing |
| 600 MHz | 8 to 10 percent | 0 | 758 GH/s | not measured |
| 575 MHz | 0.1 percent | 0 | 737 GH/s | 183 W, steady |
| 500 MHz | 0 | 0 | 640 GH/s | not measured |

The last column came from Mark and a plug-in power meter on the fifth
morning, the one number no log could see. Efficiency per watt barely moves
between a healthy factory clock and 575 MHz, 4.1 against 4.0 GH/s per watt.
What moves is the absolute spend, 18 percent lower, and on a unit with a
marginal chip, whether it hashes at all.

## What made it work

Mark set the rules first, every time, and they got looser as they held:
from "do nothing till I approve" on day one to "run the runner against the
real miner" on day four, which was a single sentence because the runner's
guards, its end clock, and its visible console had all been designed for
that sentence.

The agent's most useful contributions were findings, not code: the reset
loop and the one chip behind it, the dead fan field, the token race, the
Save button that reverts the clock, the plaintext WiFi credentials nobody
asked about, pool difficulty hiding inside shares per hour, errors lagging
a clock change. Each one was a fact read from the machine or the vendor's
own source, reported before it was acted on, and most of them changed what
got built.

Mark's most useful contributions were the questions that changed scope.
"Is there any way to build a better web page?" "What license for
cypherpunks?" "What is safe for users to press?" "I want to optimize
clean nonce rate." "Build the lessons into the app." The power reading.
None of these was a feature request in the usual sense. Each redefined
what the project was for.

Disagreement was cheap and both sides used it. The agent recommended
waiting to push and Mark pushed anyway, for a specific person, and was
right. The agent declined to change a privacy setting and offered the
evidence instead. Mark rejected a fiddly login box, a scary dialog, and the
wrong shell, each in a sentence.

And the state lived in files. Every day ended with the status document
updated and everything committed green. Every build session started from a
four-line brief. Five sessions, one controller freeze, one power cycle, and
nothing was lost. The session log that this
essay was written from is now a required file in every project Mark builds
with an agent, because reconstructing it after the fact took a morning and
writing it as they went would have taken minutes.
