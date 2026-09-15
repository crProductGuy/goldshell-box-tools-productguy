# One marginal chip: how this project was built

This is the story of goldshell-box-tools-productguy, written from the session log,
the git history, and the docs. It covers ten days in September 2026, one
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
states. The docs say outright that on this firmware the token is
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

## The plug that wasn't the miner's

The evening after the second freeze, Mark sent the agent into research and
planning mode overnight. He wanted an optional module to power-cycle a hung
miner through a smart plug. He had TP-Link Kasa plugs around the house, one of
them "already inline with this miner power plug."

The session ran unattended, so every question went into the proposal with the
assumed answer beside it. The plug probe stayed read-only, because the miner
was believed to hang off it. It did not. The plug Mark had named reported its
relay off while the miner hashed. LAN discovery found a second Kasa plug with
an energy meter reading a steady 188 W, matching the wall meter. Two commands
came out of that: `init`, which records the plug's device id and refuses to
cycle any other device, and `discover`, because the owner's recollection of
which plug was where had been wrong.

Four minutes into the trace the miner froze for the third time, 34 minutes
after the previous power cycle, and the metering plug's reading fell from 188
W to 38 W in the same minute. The identity question was answered by the
failure the module was meant to fix. At 23:27 Mark wrote, "recheck miner, I
think it's hashing now, 183W ... I don't think it needs a power cycle.
confirm." It had come back at 23:13 with the relay closed the whole time. "I
like the 15 min power cycle if apparently dead. What do you need from me to
build to the plan?"

It was built that night, test-first, from 101 tests to 165, in a worktree that
stopped short of the merge. At noon he authorized the real thing: "I authorize
you to power-cycle the Kasa when you get to that."

The next session deployed 0.3.0 and recorded the plug. `gbox power cycle`
needs the typed word CYCLE at a terminal, and the agent's shell refused piped
input, so it could not confirm the command itself. Rather than work around its
own guard, it opened a visible PowerShell window at the prompt and waited.
Mark saw it ("I saw this modal over this terminal") and typed the word. At
12:11:07 the relay opened for 15 seconds. The miner answered a good sample 66
seconds after the cut, at 575 MHz with zero board resets. Then a second
instance of the service, pointed at a LAN address that answers nothing, drove
the ladder on the clock the guide predicts: failed soft restarts at 12:18:27
and 12:32:58, the plug at 12:33:13 with the episode 17 minutes old. The live
service saw the miner back 60 seconds later. Two cycles, both authorized, both
survived at the manual clock.

## A password, a rename, and what was holding the door

The night of the research session Mark had sent a screenshot: "check the
miner. Report looks funny. Fans full but doesn't seem to be hashing. maybe
needs a soft reset?" The agent read the service's log rather than the miner.
Shares were climbing and the fans were already falling: a fresh boot, not a
fault, and a soft restart would only have wiped the firmware's history buffer.
But the agent also said "something power-cycled it at 23:13", inferred from a
42-second uptime. The research session running at the same time had the plug's
meter and knew the relay had never opened. The guess was wrong because the
other session had better evidence, and it was restated to Mark the next day.

The next afternoon he asked whether the read-out had been saved. It had not.
The raw rows were in the service's files, but the reading of them existed only
in the session. It went into the status document.

Then two chores that three sessions had deferred, each because it had started
inside the directory it was meant to rename: "do the directory rename ...
scrub the transcript." The scrub found the miner's password ten more times in
one transcript, in three encodings, and replaced them all. The rename failed
twice more, "being used by another process," with the service stopped and the
session's own shells parked outside the folder. Rather than kill processes by
guesswork, the agent wrote a short script that reads each process's working
directory and named what was standing in the door: two idle Claude Code
sessions, the console the clock trial had run in, three orphaned log monitors,
two Explorer windows, and two leftovers of the previous session's own tests.
Windows does not rename a directory while any process calls it home, and idle
agent sessions and file-manager windows count. The harness would not let the
agent end the two Claude sessions, so it asked, and Mark closed them himself.

That evening he sent another screenshot: "check out the 6-minute visible gap
in the fan speed and 2 temp traces on the graphs, from 16:22 - 16.28." The log
settled it. The gap was the service stopped for the rename attempt, and a
shorter one at 17:27 was the rename itself. The hashrate chart had no hole
because it draws from the miner's own buffer. The agent said the chart could
not tell a service outage from a miner outage and offered a marker at each
service start. "yes, please. add that," he wrote. The S glyph joined the
marker set that night, along with a boot-time promise of two to three minutes
corrected to the measured 60 to 66 seconds.

## Keep smacking it so it runs

Late on the tenth Mark asked for a plan: watts and GH/s per watt in the trials
table, a watts chart, a power tile. The next day he added a rolling log of
every intervention the software had made, right-hand percent-of-maximum axes,
and said he wanted to generalize to Goldshell's SC Lite models. "Advise and
ask me if there are any reasons to split this work." The advice was to split,
since nothing on this LAN could test another model.

While the plan was being written the watchdog ran its acceptance test three
times. Freezes at 23:43, 05:40 and 06:25, each followed by two failed soft
restarts and a power cycle, each cycle bringing the miner back in about a
minute. A fourth freeze at 10:25 met the daily cap of three cycles and the
miner stayed hung. Four freezes in eleven hours, against three in the week
before. The 0.4.0 restart then exposed a gap: the caps were in-memory
counters, so restarting the service handed the watchdog three fresh cycles on
a still-hung miner. It recovered the unit at 11:46, but a restart should not
reset a safety cap.

On the twelfth Mark swapped the 360 W power brick for a 1600 W Bitmain supply.
Ten minutes after power-on the board reset twice, the first resets at 575 MHz
since the clock trials ended. He thought the resets were noise from powering
up; the log put them ten minutes after, and he conceded: "I wasn't touching
anything at 14:55, those resets count." The draw stepped from about 182 W to
197 W at the same clock, for a cause nobody could see, because core voltage is
not in the API.

Then the instruction that names this chapter: "Make sure that the daily cap is
high enough to accommodate these controller-going-AWOL events that are now
more frequent. Uptime of hashrate is more important to me than capping
interventions. I'd be OK with 8 power-control restarts a day if needed. Keep
smacking it so it runs." Reading the ladder before raising the cap turned up
the thing that mattered. A cycle needs two failed soft restarts, and each
attempt takes a restart slot, so every cycle costs two of the watchdog's own
daily restarts. The config now refuses a restart cap under twice the cycle
cap, the caps are read back from the event log at start, and Mark's went to 20
and 8.

"0.4.1 is very good," he wrote, and asked for the ladder cut. The plug had
been moving about 17 minutes into a freeze; with both gaps at 5 minutes it
moves at about 7. 187 tests by evening.

## Power on purpose

The next session was meant to start the other-models plan. Mark's next message
redirected everything: "since manual power cycling by me or some user causes
loss of the board reset counter, what can we do to preserve that? Is there any
reason to want to put a 'cycle power now' button on the UI? ... The question
behind the question is how to keep that while enabling low-operator-friction
hard power off / on as part of normal daily operations, not just recovery from
'hung'."

The premise was wrong in a useful way. The counter was not lost by a cycle;
the service logged it every poll and summed increments across boots; only the
firmware's own since-boot figure dropped to zero. What was missing was
different. A planned outage looked exactly like a freeze to the watchdog: two
minutes unreachable, two failed restarts, and a cycle that would turn a
deliberately-off miner back on. And a cycle at the wall switch was invisible
to the log. So the case for a button was not convenience. It was that every
power event should pass through the one place that records it as the owner's
and tells the watchdog to stand down.

Mark raised the shutdown case himself, "a 'shut down now' button that informs
the software of an impending power-down ... so we don't want to have the
service reacting to an unreachable device that's unreachable for physical
reasons." That became the hold, with the buttons and a schedule as three ways
into one state. On how a hold should end he chose the miner's own return over
an explicit release, "Less user planning and cognitive load," and floated a
20-minute expiry; the agent argued that under auto-release shorter is not
safer, and proposed an hour. "Looks good, build it," he wrote. "See you in the
AM."

It was built overnight, 260 Python and 39 JavaScript tests, and deliberately
not deployed. The watchdog was the thing recovering the miner that night, five
freezes that day, and new watchdog code should not take over that duty while
nobody is watching.

Mark deployed it the next afternoon and ran the live checks with his own
hands. Then: "The unit is acting weird: low wattage, both red LEDs on, not
hashing." From the LAN the controller was up and answering, with clock 0, the
board sensor at minus 150 and 9 W at the wall: the hashboard had never come
up. The hold had released on two HTTP answers, because the rule said
"answers." "fix the release rule so it needs hashing." 0.5.1 changed that word
everywhere. The same evening, "I really want to get the Linux version out
tonight," for a friend: 0.5.2.

## So the operator can see when it went bad

The evening of the Linux installer Mark also asked for a new chart: the
average error rate and the worst chip's error rate against clock, over about
three days. "Argue with me or ask questions." The agent argued for bad share
over bad count, since a faster clock attempts more nonces; for board resets on
the same chart, since an error chart alone stays flat through a burst of
resets; and for logging every chip every poll. He took all of it, extended the
other charts to 24 hours, and said what it was for: "for the human operator to
see when things really went bad on a graph, so she/he can do something."

0.6.0 was built in that order. His first look asked for bold axis titles and
tooltips that grow left; a design pass gave three directions, and "I accept
Main." A patch the same night fixed a chart reading minus 131 degrees, the
firmware's reading for a board that was not there, and met a constraint: "I
have mediocre eyes with some cataracts, so I think the density should be
increased by 50%." Later that night the ladder's second rung was fixed: the
plug had been reaching a frozen controller at about 12 minutes where the guide
promised 7. Mark said 7.

The next morning: "have a look at the overnight run ... 7-8 adverse events
over 4 hours." The logs gave seven controller hangs between 00:42 and 04:07,
each with no log line, six of the seven within thirty minutes of a
boot. The ladder did what it now promised, the plug about seven and a half
minutes after the last good sample every time. Software explained none of the
hangs. The evidence pointed at the power path, and Mark swapped back to the
360 W supply. While the agent was reading, the board dropped off with the
controller alive and came back through a 64-reset cold start. After the swap
the board failed to start at all until a soft restart, the third time for that
signature across two supplies: the board's power-up was what was marginal, not
one PSU.

"the 64 restarts shown on the 3-day graph don't show in the 13.00-14.00
timeframe in the 24-hour graphs. This is misleading." True; the 24-hour charts
had no resets series. Then: "Shouldn't we define a Reset?" The project agreed
its vocabulary that afternoon. A board reset is the miner reinitializing its
own hashboard, counted and drawn as bars. A soft restart is the watchdog's or
the owner's. A power cycle is the plug. And one fact closed a request: the
firmware exposes no per-chip temperature anywhere, so the column Mark had
asked for cannot be filled.

## Another lab's notes, and the cost of looking

The other-models work started the night of the thirteenth, unattended, with a
warning said out loud: all of it was built against another developer's notes
for the SC Lite, not a unit. Mark's answer set two tasks. "what would I need
to tell a dev with an SC Lite about what to capture for Devs and Minerinfo,
and how to capture it? Write an MD file about that ... Go ahead and start with
Gate 1 overnight. I'm off to bed."

The capture request was written for a stranger's machine: seven read-only
requests, one at a time with a pause because of the token race, with pools,
WiFi settings, syslogs and the MAC address left out. Gate 1 gave every model a
capability profile, with an unknown model taking the SC-BOX's path and every
optional capability off, and parsed the power plan from the string the unit
itself wrote rather than from a table copied out of notes. 336 tests. It was
not merged overnight, because nothing in it was worth a night-time restart of
the watchdog that Mark had not asked for. It went in the next afternoon.

That day ran ten hours and reached 57 percent of its context window.
Screenshots were 28 percent of it and file reads 16 percent. Mark asked for
controls rather than another reminder. What followed was built outside this
repo: hooks that deny an unscaled screenshot, an unbounded read of a long
file, or a shell command that prints a long file whole. They fail open,
because a bug in a budget guard must never block every read. Then: "what could
we do to limit the high token count from claude-in-chrome ... fix the present
to fix the future." The answer was that the cost is pixels. A full-HD
screenshot is about 2,800 tokens, the same at half scale about 700, a 1280 by
800 window at half scale about 340. Browser checks now go to a subagent; a
session gets twelve screenshots; a zoom region is capped. "do the things as
you recommended."

The last session was an experiment on the controls: a cold start of the
project under them. The load cost 85k of a million-token window, about 51k of
it fixed before the first message. The session-hygiene thresholds had been
percentages, 50 and 70, which on this window meant 500k and 700k, past the
point where the previous night's session had visibly drifted. "use 250k as the
first checkpoint threshold," then "update the 2nd threshold too": 350k.
Nothing in the tool changed. 0.6.5 was live, with eighteen restarts and seven
cycles counted from the last day, and gate 2 waiting on a capture from a
friend's four-board unit.

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

Between the tenth and the fourteenth of September the toolkit went from 0.3.0
to 0.6.5 and the test count from 101 to 336. Two power cycles were proven on
the first day of the module, one by hand and one provoked through the
watchdog, and then the ladder ran unattended: four automatic recoveries in its
first twelve hours, each within about a minute of the cut, and on the night of
the thirteenth seven controller hangs in four hours, every one recovered by
the plug about seven and a half minutes after the last good sample. The last
day on record counted eighteen soft restarts and seven cycles. What the
numbers did not settle: why the controller hangs, which by then looked like
the power path and not the software, and whether the board's cold start, 64
resets one afternoon and 34 another morning, would get worse.

## What made it work

Mark set the rules first, every time, and they got looser as they held: from
"do nothing till I approve" on day one to "run the runner against the real
miner" on day four, which was a single sentence because the runner's guards,
its end clock, and its visible console had all been designed for that
sentence. By the second week the sentence was "I authorize you to power-cycle
the Kasa when you get to that," and the guard it met was a typed word in a
window the agent opened rather than bypassed.

The agent's most useful contributions were findings, not code: the reset loop
and the one chip behind it, the dead fan field, the token race, the Save
button that reverts the clock, the plaintext WiFi credentials nobody asked
about, pool difficulty hiding inside shares per hour, errors lagging a clock
change, a cycle that costs two restart slots, a hold that released on a
controller with no board behind it. Each one was a fact read from the machine
or the vendor's own source, reported before it was acted on, and most of them
changed what got built.

Mark's most useful contributions were the questions that changed scope. "Is
there any way to build a better web page?" "What license for cypherpunks?"
"What is safe for users to press?" "I want to optimize clean nonce rate."
"Build the lessons into the app." The power reading. None of these was a
feature request in the usual sense. Each redefined what the project was for.

Disagreement was cheap and both sides used it. The agent recommended waiting
to push and Mark pushed anyway, for a specific person, and was right. The
agent declined to change a privacy setting and offered the evidence instead.
Mark rejected a fiddly login box, a scary dialog, and the wrong shell, each in
a sentence.

The second half adds a fifth thing. The hardware kept being wrong in ways the
software had to record before anyone could explain them: the plug that was not
the miner's, a power supply that raised the draw by 15 W and set the board
resetting, a hashboard that failed its own power-up on two supplies, a
firmware that reports minus 150 degrees for a board that is not there. None of
these was fixed in code. Each was logged, charted, or given a name, and the
explaining came after, sometimes from Mark. His questions kept redefining
scope the way they had in the first week: "the question behind the question"
turned a button into a hold, and "shouldn't we define a Reset?" turned a chart
into a vocabulary. And the cost of looking became a rule. One ten-hour session
spent more than a quarter of its window on pictures of a page, so the pictures
now go to a subagent, the reads are bounded by hooks, and the thresholds are
numbers rather than percentages.

And the state lived in files. Every day ended with the status document updated
and everything committed green. Every build session started from a four-line
brief. Five sessions, one controller freeze, one power cycle, and nothing was
lost in the first week; twenty-three controller hangs and nothing lost in the
second. The session log that this essay was written from is now a required
file in every project Mark builds with an agent, because reconstructing it
after the fact took a morning and writing it as they went would have taken
minutes.
