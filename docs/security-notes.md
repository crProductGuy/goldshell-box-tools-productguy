# Security notes: Goldshell Box family (MCB_V5 "cloud-box" firmware)

Observed on an SC-BOX, firmware 2.2.5, on 2026-09-06. Other Box models ship
the same firmware and are expected to match. These are notes for owners,
not vulnerability disclosures; nothing here is new to anyone who has read
the firmware's own web UI.

## The password is the token, forever

- Login is `GET /user/login?username=admin&password=<hex>&cipher=true`, where
  `<hex>` is the password encrypted with a fixed key that ships in the UI.
  Anyone who sees the URL (a proxy log, a browser history) has a
  password-equivalent.
- The returned JWT has no expiry and no nonce: every login returns the same
  token, and it never stops working. Changing the password is the only way
  to invalidate it.
- Consequence for this toolkit: the service keeps the token in memory only,
  `--remember` stores the encrypted form with owner-only permissions, and
  the service never logs a request line.

## The miner stores your WiFi credentials in plain text

`GET /mcb/wifisetting` returns something like:

```json
{"enable": false, "version": "v1.0", "mode": "sta",
 "network": [{"ssid": "<your network>", "password": "<your password>"}],
 "exist": true}
```

- `mode: "sta"` is station (client) mode. This setting is about the box
  joining a WiFi network, not about the box hosting one. On the unit
  examined there was no access-point mode in the object and no network
  broadcast by the miner was seen in a scan.
- The SSIDs and passwords are stored and returned in plain text to any
  holder of the token. If the miner was ever set up over WiFi, those
  credentials stay there after switching to Ethernet.
- Hardening: with the miner on Ethernet, clear the list and keep WiFi off.
  PUT the whole object back; the firmware's settings endpoints replace the
  object, so a partial body drops fields:

  ```
  TOKEN=...   # from the login above, or from the browser's storage
  curl -X PUT "http://<box-ip>/mcb/wifisetting" \
       -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
       -d '{"enable":false,"version":"v1.0","mode":"sta","network":[],"exist":true}'
  ```

  Read it back with `GET /mcb/wifisetting` to confirm. A settings PUT
  restarts the fan daemon on this firmware, so expect a short fan spike.
- **Caveat, verified 2026-09-06: the network list cannot be changed through
  this endpoint at all.** The PUT handler (the backend logs it as
  "Wifi sta enable") only starts or stops the WiFi station daemon according
  to `enable`; whatever is in `network` is ignored. Tested with the full
  object, `{"enable":false,"network":[]}`, a blank entry, a placeholder
  entry, and a full enable-then-disable cycle with a placeholder: `enable`
  toggled each time (confirmed by read-back and by the backend log), the
  stored credentials survived every write. Whatever wrote them, most
  likely the phone app's setup flow, uses a path this API does not expose.
- The web backend echoes every PUT body, passwords included, into
  `/dbg/syslog`, which any token holder can read.
- The shipped web UI has no WiFi page at all: none of its four JavaScript
  chunks reference an SSID. The endpoint exists only for whatever set the
  credentials in the first place, most likely the phone app during setup.
- **Factory reset does not clear it.** The unit examined was bought second
  hand and had been factory reset; the stored networks belonged to the
  previous owner and had never been joined from this one. If you sell or
  pass on a Box, assume its WiFi credentials travel with it.
- The only reliable remedy is on the router side: change the WiFi
  password, and the copy inside the miner becomes worthless.
- Tooling rule: any code that prints this object must redact `password`
  inside the `network` list, not only top-level keys. A session on
  2026-09-06 got that wrong twice.

## Other things the API exposes to a token holder

- `/mcb/pools`: pool URL, worker name (often a wallet address) and worker
  password, in plain text.
- `/mcb/setting`: the unit's MAC address in `name`.
- `/dbg/minersyslog`: the cgminer log, which repeats the pool user.
- `/mcb/facrst`: factory reset, one unauthenticated-looking PUT away once
  you hold the token. This toolkit never calls it.

## Smart plugs (the optional power rung)

- A TP-Link Kasa plug on its original local protocol (port 9999) answers
  any host on the LAN with no authentication: relay state, energy reading,
  schedules, and, through `cnCloud get_info`, the e-mail address of the
  TP-Link account it is registered to. This toolkit never logs that reply
  and never writes the plug's identifiers to the repo or the docs. Anyone
  on your LAN can switch such a plug; keep it on the same trusted segment
  as the miner.
- Newer Kasa firmware and all Tapo plugs use an encrypted protocol (KLAP)
  keyed by the account credentials. This version does not drive them.
- The service never exposes the plug: no endpoint switches it, and the
  watchdog only cycles the device whose id was recorded at setup.

## The power and hold endpoints (0.5.0)

The service can now open the plug's relay for the page. The proof is the
miner's password in its encrypted form, the same string the page already
sends to the miner's own login: the service logs in with it and discards
the token. It crosses loopback in plain HTTP by default, which is no wider
than the login the page makes across the LAN. Off and Cycle need it; On
and Hold do not. On is what the watchdog already does unasked. A hold only
stops the software judging, and a hold on a running miner ends within a
minute by the two-sample rule, so it cannot silence the watchdog on a
healthy unit; on a dead one it delays recovery until it expires, which is
the same power a forged `/api/event` line never had but a LAN client with
`--bind` now does. Loopback-only by default, as before.

## Network posture

- The web backend answers any origin (`Access-Control-Allow-Origin: *`).
  That is what makes a standalone dashboard page possible, and it also
  means any web page you open on a machine that can reach the miner can
  talk to it if it knows the token.
- Bursts of roughly 15 requests per second crash the web backend, which
  restarts on its own. A hostile page on your LAN could keep it down.
- Keep the miner on a LAN segment you trust, do not port-forward it, and
  change the default password. `gbox serve` binds to 127.0.0.1 for the
  same reason.
- `gbox discover` (0.8.0) sends one `GET /mcb/status` to each address on
  the subnet, with no `Authorization` header. It reads no credential out
  of `config.json`, sends none, logs into nothing and never touches port
  4028. It is a sweep of your own network, so it is as noisy as a ping
  sweep and no noisier: one short HTTP request per address, nothing
  logged per address, and it refuses a range wider than 1022 hosts. What
  comes back is untrusted -- any host on the LAN can answer port 80 --
  so the model, firmware and hardware strings are stripped of
  non-printable characters and cut to 40 before anything prints them.

## The service answers only to its own name (0.10.1)

Binding to 127.0.0.1 keeps other machines out, not other web pages. Up to
0.10.0 a page on any site could use DNS rebinding: its name is re-pointed
at 127.0.0.1, the browser treats the service as the page's own origin, and
the page can read everything and send `/api/hold` with no expiry, a junk
`/api/token`, or `/api/power` On. Verified on the live 0.10.0 service: a
request with `Host: attacker.example:8765` got 200. Off and Cycle were
never open this way, because they need the miner's password.

- Every request must carry exactly one Host header, and it must be
  `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` or the `--bind`
  address with its port, matched whole. Anything else gets 403 before
  any route runs. A rebinding page still sends its own name, so it is
  refused. An SSH tunnel arrives as `localhost` and works.
- A wildcard bind (`0.0.0.0`, `::`) adds no name, since no browser sends
  one. Such a service answers remote browsers with 403: reach it through a
  tunnel, or bind the machine's own address.
- `/api/hold/release` now needs `application/json` like every other write.
  Before this a page on any site could release a hold with a blind form
  POST, no rebinding needed. A JSON POST from another site needs a CORS
  preflight, and the service answers `OPTIONS` with 501.
- **What it does not do:** it does not stop anything that can reach a
  `--bind` port directly. A device on the LAN sends whatever Host header it
  likes. The warning `gbox serve` prints for a non-loopback bind still
  stands: there is no login, so use a firewall, a VPN or an SSH tunnel.

## What the 0.8.0 security pass changed, and what it deliberately left

One adversarial review at feature-complete, 2026-09-20, over everything gate 3
added. Five findings were fixed in the same session; the rest are written down
here rather than built, because each costs more than it buys today.

Fixed:

- **A sweep of the wrong network.** `gbox discover` with no flags asked the
  default route which address to sweep. On a machine running a VPN client the
  default route is the tunnel, so the answer was a public address belonging to
  the VPN provider, and a bare `gbox discover` would have sent one request per
  address into a stranger's /24 through the tunnel. Measured on the machine
  this was built on: the route named a public address belonging to a VPN
  provider while the miner sat on an ordinary private LAN. The route is still
  asked first, but a routed address that is not private is discarded in favour
  of this machine's own private addresses, and when there is none the command
  asks for `--subnet` instead of guessing.
- **`--subnet` accepted any range on earth**, including public ones. It now
  refuses anything that is not a private IPv4 network, and says why. An IPv6
  CIDR is refused outright rather than sweeping addresses the probe cannot
  build a URL for.
- **Redirects were followed.** One LAN device answering the probe with a
  `Location` header could send the sweep to an address the operator never
  named. A 3xx is now a miss.
- **The skip of the configured miner was defeated by a scheme or a path** in
  `config.json`'s `host`, which nothing validates. Host matching now
  normalises both sides. The related case it cannot fix is worth knowing: the
  usual reason to run `discover` is that the miner moved, and then the
  configured address names where it used to be, so the sweep reaches it at its
  new one while the service polls it. The command now says so when a service
  is running, and names the subnet and the address count before it starts.
- **Rotation could loop on every poll.** When the carried window is larger
  than the cap -- reachable with settings the config accepts, such as a 5 MB
  cap with a 72 h window at a 10 s poll -- the fresh file is born over the cap,
  so the next poll rotates again, each rotation replacing `log.csv.1` with the
  rows it has just carried. The history the first rotation archived would have
  survived one poll cycle. Rotation of that file now stops after one such
  rotation, with a line naming the two settings to change; the same guard
  covers `events.log`.
- **Event lines carried full paths.** A Windows `PermissionError` text names
  the data directory and the account, and the event log is served to the
  dashboard, which matters on a `--bind` deployment. Deferred-rotation lines
  now name the kind of failure and the file, never the path.

Left, with reasons:

- **A slow host can stall a sweep.** `--timeout` is a per-socket-operation
  timeout, not a budget, so a host that answers one byte at a time holds its
  worker open; `sweep` waits for every target. The read is capped at 64 KB, so
  memory is bounded and only time is not. A per-address deadline needs a
  supervisor thread, which is more machinery than a LAN sweep of 254 addresses
  is worth. Stop it with Ctrl-C.
- **No `fsync` in either rotation.** `os.replace` is atomic for ordering but
  not durable, so a power cut in the wrong millisecond can leave a short
  `log.csv` with the archive already in place. The fix costs a flush on a
  multi-megabyte file on a machine that is also polling a miner.
- **A symlinked `log.csv`** would have its link replaced rather than its
  target. Nobody has one.
- **Two services sharing a data directory** take no cross-process lock. On
  Windows the loser gets a `PermissionError` and defers, which is safe; on
  POSIX the two could interleave. `gbox.pid` exists to stop that arrangement
  in the first place.

## The LAN rules, and why address-based checks were not enough (0.8.1)

The 0.8.0 pass fixed a sweep that followed the default route onto a VPN by
refusing ranges that are not private. That was the right fix for the case in
front of it and the wrong test in general: **a corporate or WireGuard VPN hands
out RFC1918 addresses**, which are as private as a home LAN and just as wrong to
sweep. The owner spotted it. The test is now what the interface *is*, read from
the operating system, and the decision moved into `gbox/netiface.py`, which
holds nine rules and the tests that enforce them. The headline ones:

- A tunnel is never a source of an address range: tunnels are recognised by
  interface type, medium, driver and name, not by the address they carry.
- The prefix comes from the interface. Nothing assumes /24 any more.
- The routing table is not consulted at all; a test reads the module's own
  source to keep it that way.
- 169.254.0.0/16 is never a network, in either direction: never a candidate,
  and refused as an explicit `--subnet`. It means DHCP did not answer.
- A disconnected link or an address the OS has stopped preferring is not a
  network, so a stale lease on an unplugged adapter cannot become a sweep.
- A LAN that overlaps a tunnel's range is refused rather than preferred: which
  way a packet would leave is not knowable from here.
- A sweep where every address is unreachable reports a dead network and exits
  2, rather than reporting "no miner found" and sending the owner to look at a
  miner that is fine.

None of this needs the internet, and none of it resolves a name: a machine
whose uplink is down still knows its own LAN, which is the only question being
asked.

## The miner's log is kept as labels, never as text (0.9.0)

The miner truncates its own log within hours, so the service now keeps a
summary of each read in `minerlog.csv`. The log repeats the pool user in its
start banner, and on some pools the user is a wallet address. Two designs
were on the table.

**Mask and store the lines** (strip anything that looks like a user, a URL
or a long token). That is a blacklist, and a blacklist fails open: the first
line shape nobody anticipated is written to disk whole.

**Match known shapes and store our own words.** This is what was built.
`classify_syslog` holds a fixed table of line shapes. A line that matches
becomes a label from that table, a count, and two timestamps. A line that
matches nothing is counted as `other` and its text is dropped. Nothing of the
miner's text reaches the file, a terminal or the event log except the two
timestamps, and a test feeds the classifier an invented user and token and
asserts neither comes back.

The timestamps are the one place the miner's own characters are written, so
they are held to a narrow shape. The security pass found that `\d` in a
Python pattern matches any Unicode digit, and that nothing checked the date
was real, so Arabic-Indic digits or `9999-99-99` would have reached the file.
The pattern is now ASCII-only and each timestamp must parse as a date. A line
stamped later than the log's own last line is also skipped: a clock glitch
into the future would otherwise become the reading cursor, hide every real
line after it, and make the file count the same lines again on every other
read.

The input is megabytes of text from a device on the LAN, so the patterns
are anchored at the start of the message and none nests a quantifier. Each
label pattern sees only the first 200 characters after the timestamp; the
timestamp pattern itself runs on the whole line, and is anchored and linear.
The file stops growing at 5 MB and says so once in the event log; a device
that floods its own log cannot fill the disk through it. The size is checked
on every read, so moving the file aside starts a new one without a restart.
A classifier failure is counted and costs neither the sample nor the
temperature reading.

Not bounded, and not new in 0.9.0: the log read itself (`Miner.syslog`) has
no size cap, so a hostile device could send far more than the firmware's
usual 4 MB. Memory and time grow linearly, on the poller thread. A plain
`read(n)` cap would keep the oldest part of the log and drop the newest, which
is the part every reader of it needs, so this is left for a design decision.

The board-absent rule added in the same release is a new reason for an
action the watchdog could already take (a soft restart), under the same
daily cap and the same gap between restarts. It cannot cut power: the plug
is considered for one reason only, a miner that is unreachable. A device
that faked the signature could cost itself `max_restarts_per_day` restarts,
which it could already do by freezing its share counter.

## Reading the log by position, and what that leaves open (0.9.1)

After a cold boot the miner's clock reads 2007 until it reaches a time
server, and the log keeps the run before. 0.9.1 finds the reading cursor by
position (the last line stamped exactly as the cursor) instead of by
comparing stamps, and keeps temperatures only after the newest run start,
matched at the start of a message so a pool user containing the phrase cannot
move it. One review pass found nothing critical or high. Two findings are
left open:

- **A miner that never gets network time.** Every boot then restarts at the
  same 2007 stamps, so a cursor taken before the clock was set can match the
  same stamp in a later boot, and the rest of the earlier boot's lines (the
  ones that say why it rebooted) are skipped. The 0.9.0 comparison lost lines
  in the same case too, different ones. A fix needs a position hint beside
  the stamp, which is a design choice; the owner's unit gets its time set.
- **`board_source` "auto" undecided.** Until `/dbg/minerinfo` first answers,
  each poll probes port 4028 and then tries `/dbg/minerinfo`: two requests in
  sequence, still one in flight, only from a service start until the miner
  first answers. A service that starts while `/dbg/minerinfo` is up but port
  4028 is not yet still settles on `/dbg/minerinfo` for the run (unverified
  whether a boot opens them in that order).

## The log cursor is a place in the log (0.10.0)

The first of the two findings above is closed. Both log cursors (chip
temperatures and `minerlog.csv`) are now a place: the index of the last line
read, the number of lines the log had then, and a SHA-256 of that line. The
line's text is never held, so a cursor that reaches a traceback or a `repr`
carries nothing of the pool user. A read trusts the cursor only when the log
still has at least that many lines and the line at the index hashes the same;
then it reads from the next line. That is exact for a log that is only
appended to, whatever the miner's clock did, and it reads a line written
later in the same second as the cursor, which 0.9.1 missed.

Otherwise (the first read of a service's life, or a log that was truncated
or rewritten) the read scans backward from the end and stops at the newest
process banner (`Started intminer`), at a stamp that goes up while scanning
backward (the far side of a clock set back), or five minutes before the
log's last stamp, whichever comes first. The board's init line is not a stop:
the miner re-inits its board mid-run after a fault, and the lines before it
are the ones that say why. No miner timestamp is compared across a boot or a
clock jump. A line still being written when the log is read (no line end
yet) is left for the next read.

Three consequences, each covered by a scenario test:

- A line stamped in the future (a clock glitch) no longer needs to be
  skipped. It cannot hide later lines, because the cursor is not a time. It
  is counted with its stamp, and a first read stops at it as at any jump.
- After a truncation, the stretch the fallback finds may still hold lines
  the cursor already read. Only when that stretch is one run with no clock
  jump, and the cursor's stamp is not later than its last line, are its lines
  at or before the cursor's stamp dropped. A new line in the same second as
  the cursor is dropped with them in that case.
- **Accepted residual:** a truncation AND a boot with no time server between
  two reads, with the new boot's banner cut from the log, can duplicate or
  drop a few lines. The miner writes about one line every five seconds and
  the log is read every five minutes, so this needs two rare events inside
  one read interval.

Not verified: whether the firmware's log ever ends without a line end. If it
always does, each read leaves its newest line to the next one: correct, one
read late for that line.

## Acting only on evidence that points at the miner (0.10.0)

Three rules added in 0.10.0 can only stop the watchdog from acting. None of
them adds a restart or a cycle, apart from the one bounded exception in the
first.

**The pool (B).** A stall, or an unreachable miner whose web backend still
answers, is not restarted when the newest read of the miner's log shows
`pool_not_responding` or `stratum_interrupted` since the accepted counter
last moved, or shows the miner probing for a pool since its newest process
start with no share accepted. Before judging, the verdict waits up to three
polls for a log read taken after it. The read is every `syslog_interval`, and
the measured outage wrote its pool lines two to two and a half minutes in,
which a read can miss. During such an episode the service also reads the log
when port 4028 is dead, on the usual schedule, one request at a time. The
evidence ends when the accepted counter rises on two samples at least a
minute apart, when a line that points at the miner itself (a chip, board or
process fault) comes after the pool line, or at a process start. A log
"Accepted" line does not end it, and neither does one bump of the counter,
because two shares were logged eight minutes into the measured outage. A
counter that falls (a restart sets it back to 0) is not a share. After
`upstream_restart_hours` (default 8, 0 never) one soft restart is let
through, counted against the cap. A service started mid-outage looks back 30
minutes of the miner's log for this evidence (not past the newest process
start), because the log goes quiet about 8 minutes in; the rows written to
`minerlog.csv` keep their 5-minute window. The 8 h clock itself is not kept
across a service restart, so a restart of the service starts it again: that
can only delay the one restart, never add one.

- **Accepted residual:** a brief pool blip read in the last log read before
  a genuine hang, with no fault line written after it, holds the watchdog for
  up to `upstream_restart_hours`. The known hangs of 2026-09-21 and 09-22
  wrote fault lines and took the web backend down with them. Either one
  releases the hold. Since the review fixes one bump of the counter no
  longer spends a pool line, which widens the window.
- **Narrowed in 0.10.1:** a service started within 30 minutes of a blip
  that had already cleared used to read the blip again and hold for the
  full 8 h. A first read now spends a pool line when the log shows share
  lines spanning at least a minute after it, by the log's own stamps, the
  same span the counter needs. A step between share stamps that goes back,
  or forward by more than 5 minutes, starts the span again, so a clock
  jump is not a minute of shares. The measured outage (two shares 30 s
  apart, then silence) is still held. What remains is a service started
  after a blip whose shares, in the log, span under a minute.
- **Accepted residual:** two shares let through by a dead pool at least a
  minute apart end the episode, as one did before the review fixes.
- A single failed log read during an unreachable episode reads as "backend
  dead", so the normal ladder resumes and may send one soft restart that was
  not needed. It is counted like any other.
- The rule needs the log read. With `syslog_interval` 0 it is off and the
  watchdog behaves as in 0.9.1.

**The meter (C).** Just before a cycle, a reading at or over `idle_watts` (a
working miner, a dead path) or under `unpowered_watts` with the relay on (no
load behind the plug) stops the cycle, with one line per episode. A plug
without a meter, or a reading between the two, behaves as before.

**The path (E).** Before a soft restart of an unreachable miner, the episode
is "can't see" when the configured plug does not answer either, when the
plug's meter reads at or over `idle_watts`, or when this computer's own link
to the miner's LAN is down. That last test (`netiface.link_up_for`) reads
only the interface table, under the module's rules: no socket, no routing
table, no name resolution. It is asked at most once a minute. Nothing is
sent and nothing is counted, with one line when the episode begins and one
when it ends. When the path comes back with the miner still dark, the ladder
starts from zero, so an outage cannot leave the daily caps spent for the
bring-up. The plug reading is the one the poller already takes each poll, so
the rule adds no request to the plug. Without a plug, only the link test
applies. An unknown answer (the OS reports no interfaces, or the miner's
host is a name) counts as "can see", which is today's behaviour.

**Limit: the miner must be on this computer's own subnet.** The link test
asks whether a connected interface's network contains the miner's address.
A miner reached through a router, on another VLAN or subnet, fails that test
on every episode, so an unreachable miner there is held as "can't see"
forever: no restart and no cycle, with one line per episode. Until 0.10.x
handles routed miners, keep the service on the miner's own segment.
(Found in the 0.10.0 review.)

Open, not provoked: whether a boot with no pool can match the board-absent
signature (clock 0, board sensor at -150). That rule is unchanged, and it
only ever sends a soft restart under the cap.
