# Stale-share indication for the gbox dashboard: design proposal

Written 2026-10-05 by a design session (Claude Fable 5.1, session 27f96163 fork), read-only against the repo at
`main 4d232db`. Nothing in the repository was changed. Prototype: `stale-shares-prototype.html` beside this file; evidence of
every check: `EVIDENCE.md`. Artifact link: see section 10.

**Status: a proposal. Not reviewed, not decided, not built.** The owner has not yet evaluated it, and the six
decisions in section 7 are open. It is published here so another developer can read it. `EVIDENCE.md`, which this
document cites, holds raw command output from the owner's machine and is not published.

To see the prototype, download `stale-shares-prototype.html` and open it in a browser; it needs no server and no
network. GitHub shows an HTML file as source, not as a page.

Every factual claim below carries one of: **(verified: file:line or command)**, **(given: brief)** for facts the
brief established and this session did not re-derive, or **(unverified)**.

## 1. The problem

The miner answers the pool's puzzles and the pool accepts or rejects each answer. The dashboard counts both on the
Shares tile ("rejected N") (verified: `gbox/web/index.html:49`, `gbox/web/app.js:1118`). A rising reject count
looks like a miner problem, and on 2026-10-05 an agent read five rejects in one day as "a number drifting the wrong
way" until the miner's own log showed every one was a stale share (given: brief). The page gave no way to tell
the harmless kind from the kind that would matter, so a person has to go and read the miner's log to find out.

A **stale share** is a correct answer that arrived too late. The pool moves to a new block every few minutes, and
an answer to the old block that reaches the pool a moment after the switch is refused with the reason
`stale-prevblk`. The miner did its work right; the timing between it and the pool lost. On this unit every reject
line in the miner's log has had that one reason and sits in the same second as a `Work restart!` line, the
moment the pool handed out new work (given: brief). Hardware trouble shows up elsewhere, as hardware errors and
bad nonces, and both stayed at zero through all of it (given: brief). So the page needs to say, without the
reader knowing any of this: these rejects are the harmless kind, the rate is normal, and nothing else happened.

## 2. What a person needs to see

At a glance, the page must answer, in this order:

1. Are my rejected shares harmless? (yes, all stale / no, N were something else)
2. Is the rate normal for this miner? (a number with its own baseline next to it)
3. Did anything that is NOT a stale share happen, and when?
4. Did gbox actually see the reason for every reject, or is some of the count unexplained?

And it must stay quiet when the answer is "yes, normal, no, yes". A normal day should add no colour and no
marker to the page.

Question 4 exists because of how the data arrives: `log.csv`'s `rejected` counter moves on every 30 s poll,
while the reason comes from the miner's log, read every 300 s, which the miner truncates on its own (verified:
`gbox/api.py:301-302`, `gbox/poller.py:15-21`). The two can disagree, and the page must not paper over that.

## 3. The proposed design

Three places, all existing patterns of the page. See the prototype for each state.

### 3a. The Shares tile (the glance)

The tile's second line changes from "rejected 23" to one of:

| State | Second line | Tile colour class |
|---|---|---|
| normal | `rejected 23 · all stale, 0.07 % of accepted` | none |
| none yet | `rejected 0` | none |
| not stale seen | `rejected 24 · 1 NOT STALE at 11:03 · 23 stale` | `serious` (same class the HW error tile uses at 8 %) |
| stale rate high | `rejected 41 · all stale · 1.2 % of accepted, above the usual 0.1 %` | none (text only; see section 6) |
| unexplained | `rejected 25 · 23 stale · 2 not seen in the miner's log` | none |

The words carry the state; the colour only underlines it, as the status badge already does ("state always
carries a word, never color alone", verified: `gbox/web/app.js:1085`). The `serious` class colours the big
number (verified: `gbox/web/style.css:27`), which is the same treatment the HW error and Board resets tiles use,
so a reader who has learned one tile has learned this one. The tile's `title` attribute gets the plain
definition from section 1, like the Hottest chip tile's hover text (verified: `gbox/web/index.html:47`).

The denominators: percent is rejects over accepted in the same 24 hours from `log.csv` within-run deltas, the
same rule the HW error tile's "last hour" uses (verified: `gbox/web/app.js:529-543`). "Since boot" is the
miner's own cumulative counter, which the tile shows today; I keep the big number as it is.

### 3b. A "Rejected shares" strip in the Errors section (the record)

The Errors section already draws three days of bad share, clock and board resets per half hour, with a facts row
above it (verified: `gbox/web/index.html:86-93`, `gbox/web/app.js:1280-1310`). Rejects belong there, next to
the other "is the miner producing bad work" evidence, not on the hashrate or fan charts. Two additions:

- **One fact tile** in `errfacts`: key "Rejected shares, 3 days", value the count, sub-line "all stale ·
  0.08 % of accepted" or "1 not stale, Tue 11:03 · 23 stale". Class `serious` when a non-stale reject is in the
  window, matching `.fact.serious` (verified: `gbox/web/style.css:53`).
- **One strip under the errors chart**, time-aligned with it: one mark per reject at the time gbox learned of
  it. Stale shares draw as short hollow ticks in the muted ink colour; a non-stale reject draws as a filled
  triangle in the critical colour with the word "not stale" beside it; a reject the miner's log did not account
  for draws as a `?` glyph, the same glyph the uptime section uses for "not seen" (verified:
  `gbox/web/app.js:1567`). Shape and a word distinguish the kinds, never colour alone; see the palette note
  below. Hover gives the time, the kind, and the label.

The strip is a dot strip, not bars, because the quantity is a handful of events per day and a bar of height 1
or 2 per half hour reads as noise. At the measured rates it holds 0 to 5 marks a day (given: brief), so marks
rarely collide; two in the same second (10:12 on 2026-10-05) draw as one mark with "x2" in the hover.

### 3c. An event line, and so a marker, only for the non-stale kind (the alarm)

The poller writes to `events.log` when a log read yields a reject whose reason is not stale:
`shares: rejected, not stale (share_rejected x1)`. That line follows the `fans: high` precedent from 0.10.2:
a miner-observed condition written by the service, which the page turns into a marker on the 24-hour charts and
a row in the key (verified: `gbox/markers.py:265`, `gbox/web/app.js:313,326,359`). Proposed glyph `X`, key
text "a rejected share that was not stale". A stale share writes no event line and gets no marker: on the
measured rates that would be 20 to 25 lines over ten days of nothing, and the markers row exists for things
worth a look.

### What I chose not to show, and why

- No reject marker on the hashrate or fan charts for stale shares. They are pool timing and carry nothing about
  the miner; a letter every few hours would train the reader to ignore the markers row.
- No new chart section. The Errors section is where "is the miner producing bad work" already lives, and a
  fourth section for five events a day would outweigh the information.
- No count of `Work restart!` lines or any correlation display. The correlation is how the finding was made,
  not something the reader needs each day. If a non-stale reject appears, the event line sends them to the
  miner's log anyway.
- No status-badge state for stale shares. The badge is for things that stop hashing (STALLED, RESET LOOP, HOT,
  verified: `gbox/web/app.js:1086-1090`). A non-stale reject is not that; the tile and the marker are enough.

### Colour and quiet

The page's own status colours fail the colour-blind check between good (green) and warn (amber) in light mode:
protan ΔE 1.7, far under the floor of 8 (verified: `validate_palette.js` run in `EVIDENCE.md`). So the design
never puts "stale" in one colour and "not stale" in another and leaves it there: stale uses the muted ink and a
hollow shape, not stale uses the critical red, a filled shape, and the word, and the tile and fact carry the
word too. In the normal state nothing is coloured at all.

## 4. Alternatives considered

- **A stale marker on the 24-hour charts for every reject.** Rejected because it makes the quiet state noisy
  and because the markers row is the page's "something was done or went wrong" row (verified: `app.js:308-309`).
- **A separate "Shares" section with an hourly bar chart of accepted and rejected.** Rejected because accepted
  shares per hour already have a column in the trials table, and a bar of 0 to 2 rejects per hour against 140
  accepted is invisible. It answers "how many" when the question is "which kind".
- **Only wording on the Shares tile, no data-path change.** Cannot work: the tile knows the count but not the
  reason, and the reason exists only as `other` rows in `minerlog.csv` today (given: brief).

## 5. Data path

Privacy rule kept throughout: nothing of a log line's text is stored or served; a reason becomes one of the
module's own fixed labels, and a line with a reason not on the list goes to an explicit bucket (verified: the
rule and the `other` bucket, `gbox/api.py:378-382`).

**`gbox/api.py`, classifier.** Two labels added to `_SYSLOG_LABEL_RES` (verified table: `gbox/api.py:306-326`),
in this order, before any existing entry that could match a share line (none does today; `Accepted ` is routine
and `Rejected ` matches nothing, which is why it lands in `other`):

- `share_stale`: message starts `Rejected `, then anything, then `(stale-prevblk)`. One quantifier, no nesting,
  matched on the first 200 characters, as the table requires (verified: `gbox/api.py:304-305`).
- `share_rejected`: message starts `Rejected ` and did not match the line above. This is the explicit bucket
  for a reason not on the list, or no reason at all. Its text is dropped like every other line's.

Neither label joins `FAULT_LABELS` or `POOL_LABELS` (verified: `gbox/api.py:333-336`), so the watchdog's ladder
never moves on a reject; a reject is not evidence the miner is hung. Whether a `Rejected` line should count as
pool-alive evidence the way `Accepted` does (`_ACCEPTED_RE`, verified: `gbox/api.py:345`) is an open question
(section 9); I propose not, to keep this change out of the watchdog.

**`minerlog.csv`.** No format change; the two labels appear in the `label` column with their counts and the
miner's own first/last stamps, like every other label (verified: `MINERLOG_COLUMNS`, `gbox/poller.py:70`). The
row time is the service's read time, so a reject's time is known to within one `syslog_interval` (given:
brief). Existing `other` rows stay as they are; the page treats history before the upgrade as "not seen in the
miner's log", which is the honest reading.

**`log.csv`.** No change. The `rejected` counter (column 9) is already there and already cumulative per run
(verified: `gbox/poller.py:45-46`). The page's 24-hour percent comes from within-run deltas of it against
`accepted`.

**`events.log`.** One new line kind, `shares: rejected, not stale (share_rejected xN)`, written by the poller's
`_append_minerlog` path when a read yields `share_rejected` rows. Rate-limited to one line per log read, which
the read interval already gives.

**HTTP API.** `GET /api/series` buckets gain `rejected` (within-run delta of column 9, the same `inc` rule as
`bad`, verified: `gbox/series.py:137-142`) and the response gains a `rejects` list: `[{t, kind, count}]` with
`kind` in `stale`, `other`, `unseen`, built from `minerlog.csv` rows inside the window (`share_stale` →
`stale`, `share_rejected` → `other`) plus one `unseen` entry per bucket where the `rejected` delta exceeds the
labelled count. `events` already carries the new event line unchanged (verified: `gbox/series.py:187-197`). The
page keeps reading the one endpoint it reads for the Errors section today.

**The page.** `app.js`: the Shares tile text and class; `errorFacts` gains the reject fact; the strip under the
errors chart; `eventMarkers`' regex, `markerRow`, `markerGlyph` and `MARKER_KEY` learn `shares: ` (verified:
`gbox/web/app.js:313,322-329,355-365`). `index.html`: the Terms section gains "stale share" and "rejected
share" entries, and the Shares tile a `title`. The note under the errors chart gains one sentence.

**Version.** This changes the HTTP API (`/api/series` gains fields), so the repo's rule requires a minor bump:
0.12.0 (verified: `AGENTS.md:137-138`). The log formats do not change.

## 6. Thresholds and wording

What is known: on this unit, 25 of 34,164 accepted (0.073 %) over ten days at 550 MHz, 19 of 33,133 (0.057 %)
at 525, 5 of 3,130 (0.16 %) in 22 hours (given: brief); per day over the last twelve days, 0 to 5 rejects
against about 3,250 accepted (verified: awk over `~/.gbox/log.csv`, `EVIDENCE.md`). Five events in a day is
inside what a 0.07 % rate produces by chance, and so is zero. No day-to-day threshold on the stale count is
meaningful at these numbers, and the design does not set one.

Defaults proposed, all guesses unless marked:

- **"Normal"**: no non-stale reject in the window, and the stale rate over the last 24 h under 1 % of accepted.
  The 1 % is a guess; it is about ten times this unit's measured rate and far under the few percent pools
  commonly treat as a problem (unverified; a pool-side convention I did not source).
- **"Above the usual"** wording (text only, no colour): stale rate at or above 1 % over 24 h **and** at least 20
  rejects in that window, so a quiet night with 3 rejects out of 200 accepted does not trip it. Both numbers
  are guesses. The "usual" number printed beside it is this miner's own rate over the last 7 days, so the
  comparison is to itself, not to a constant.
- **Non-stale reject**: any count ≥ 1 in the 3-day window marks the tile and fact `serious` and draws the
  marker. Not `critical`: one such reject proves nothing about hardware on its own; the event line sends the
  reader to look. This is a product choice, section 7.
- **Unexplained reject**: shown in words as "not seen in the miner's log", never coloured. Expected after a
  service start (the first read looks back 5 minutes, verified: `gbox/poller.py:54`) and after a log truncation.

Wording, for the Terms section:

- **stale share**: a correct answer the pool refused because it arrived after the pool had moved to the next
  block. Pool timing, not the miner. A small share of them is normal; this miner's own rate over the last week
  is printed beside the count.
- **rejected share, not stale**: a share the pool refused for any other reason, or one whose reason the
  miner's log did not say. Rare, and worth a look at the miner's log when it happens; it is marked `X` on the
  charts.

## 7. Decisions for Mark

1. **Where the alert lives.** Recommend: Shares tile goes `serious` plus an `X` marker plus a fact in Errors,
   no status-badge change. Alternative: also a `warn` badge state "REJECTS · N not stale"; louder, but the badge
   is for things that stop hashing.
2. **What counts as stale.** Recommend: exactly `(stale-prevblk)`, everything else to the explicit bucket, so
   a second reason text this firmware can write is noticed and added deliberately. Alternative: any reason that
   starts with `(stale` counts as stale; broader, but it assumes reason texts nobody has seen.
3. **Stale shares on the markers row.** Recommend: none; only non-stale rejects get an event line and marker.
   Alternative: a quiet marker for every reject; more complete, noisier.
4. **The "above the usual" wording.** Recommend: words only, with the miner's own 7-day rate printed, no
   colour. Alternative: `serious` colour on the tile at the same threshold; it would have coloured nothing in
   the last twelve days, so it buys little and risks a false alarm from a bad pool night.
5. **The threshold numbers** (1 % and at least 20 in 24 h). Recommend: ship them as guesses in the Terms text
   and revisit after a month of `share_stale` rows. Alternative: no threshold at all, just the rate and the
   7-day baseline side by side; simplest, but then nothing on the page ever says "this is higher than usual".
6. **Whether a reject counts as pool-alive evidence for the watchdog.** Recommend: not in this change; the
   watchdog is untouched. Alternative: treat `Rejected` like `Accepted` in `_signal`; more correct, touches a
   tested path for no measured benefit yet.

## 8. Implementation outline

Sized for one build session; gate 2 can split off if the strip takes longer than expected.

**Gate 1: classifier, log, event line (Python).**
- Add the two labels and the event line. Fake miner: `tests/fake_miner.py` serves `dbg_minersyslog.txt` from a
  capture directory (verified: `tests/fake_miner.py:68,159`); the fixture gains synthetic `Rejected ... INCS 0
  Diff 1/1 (stale-prevblk)` lines each in the same second as a `Work restart!` line, one `Rejected ... (made-up
  reason)` line, and one `Rejected` line with no reason at all. No real ids, no pool user.
- Tests (unittest, as `tests/test_markers.py` is structured, verified: classes at `tests/test_markers.py:39-470`):
  each fixture line lands under the right label; a reason not on the list lands in `share_rejected`, never in
  `other` and never with its text anywhere in the output; `FAULT_LABELS` and `POOL_LABELS` are unchanged; the
  poller writes exactly one `shares:` event line per read with non-stale rows and none for stale-only reads;
  the watchdog's `observe_log` sees no new signal.
- Done-when: `python -m unittest` green; a run against the fake miner with the fixture writes `share_stale` and
  `share_rejected` rows to a scratch `minerlog.csv` and one `shares:` line to a scratch `events.log` (output in
  the evidence file).

**Gate 2: series endpoint and page.**
- `series.py`: `rejected` per bucket and the `rejects` list; the `unseen` rule. Version 0.12.0.
- `app.js`: tile, fact, strip, marker. Tests in `tests/app_test.js` style (Node, verified: header
  `tests/app_test.js:1-3`): the tile line for each of the five states in 3a from fixed inputs; `errorFacts`
  with and without a non-stale reject; `eventMarkers` picks up the `shares:` line; `markerGlyph` returns `X`;
  the Python/JS mirror test still passes.
- Done-when: both test runners green; the fake-miner service shows the tile text and the strip in a page check
  by a browser-checker subagent for scenarios (a) and (c); `git diff --numstat` shows no line-ending churn.

**Docs:** `docs/firmware-api.md`'s log section gains the reject line shape (shape only, no ids); README's
dashboard list gains one line; the evolution log gets the entry.

## 9. Open questions and what could not be verified

- What other reject reason texts this firmware can write: unknown (given: brief). The explicit bucket is the
  answer until one is seen.
- Whether `/api/series` reading `minerlog.csv` on each recompute is cheap enough: the file is capped at 5 MB
  (verified: `gbox/poller.py:71`) and the endpoint is cached on `log.csv` change (verified:
  `gbox/server.py:81-92`), so it should be, but not measured.
- The 1 % and "at least 20" thresholds and the pool-side convention they lean on: unverified guesses.
- Whether a reject should count as pool-alive evidence: unresolved, decision 6.
- The 45 existing `other` rows in `minerlog.csv` (verified: awk count, `EVIDENCE.md`): how many are rejects and
  how many are other unknown lines is not knowable from the labels; only a future log read would say.
- The prototype was checked by a headless DOM dump and a script syntax check, not opened in a browser by a
  person; layout at phone width is unverified by eye.

## 10. Artifact

The design session also published the prototype as a page private to the owner's account; that link is not
reproduced here because nobody else can open it. The page
copy differs from `stale-shares-prototype.html` only in dropping the document skeleton the Artifact host wraps itself.
`stale-shares-prototype.html` opens from disk with no server and no network.
