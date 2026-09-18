# Proposal: auto power-cycle of a hung miner through a smart plug

Drafted overnight 2026-09-09 to 09-10 for the owner's review. Research and
design only; no code was written. The implementation plan is a separate
file, written in plan mode, and waits for approval.

This file names no address, credential, or account. The probe output that
carried those stayed in the session.

## 1. The headline findings, in order of importance

1. **The plug named as inline with the miner is not the miner's plug.** The
   HS-105 answered on the legacy port and reported its relay **off**, uptime
   zero, named for another household device, while the miner was hashing at 575 MHz with
   26 minutes of uptime. A miner cannot draw power through a relay that is
   open. Nothing was switched; every query tonight was a read.
2. **A second Kasa plug on the LAN is an HS110 with an energy meter**, relay
   on, named for a different household load, reading a rock-steady
   187.7 to 188.0 W at 120.6 V across four samples 5 s apart. That matches
   the Kill A Watt's 183 W at 575 MHz within meter tolerance. Its relay has
   been on for 6.5 days, so last night's power cycle was not done through
   it. **It is the miner's plug:** its reading fell to 38 W in the minute
   the miner froze for the third time, 22:22 (section 3).
3. **No HS-105 of any hardware version has an energy meter.** The unit here
   answered "module not support" to both emeter queries, and TP-Link's
   community staff say the same for the model. The Kasa models that do
   meter: HS110, HS300 (six switched outlets), KP115, KP125, KP125M, EP25.
4. **Both plugs here run the old firmware and answer the unauthenticated
   port-9999 protocol.** Port 80 is closed on the HS-105, so neither has
   moved to the newer KLAP protocol. The whole legacy protocol is about 60
   lines of standard-library Python; the probe used tonight is that code.
5. **Anyone on the LAN can read a Kasa plug's cloud account name** through
   the same unauthenticated port (`cnCloud get_info`). Worth a line in
   `docs/security-notes.md`; it is TP-Link's design, not ours.

## 2. What the probe learned (read-only, 2026-09-09 22:15 EDT)

| | HS-105 | HS110 |
|---|---|---|
| Hardware, firmware | hw 1.0, fw 1.5.6 (2019-11) | hw 1.0, fw 1.2.6 (2020-07) |
| Ports | 9999 open; 80 and 443 closed | 9999 open |
| Relay | off, on_time 0 | on, on_time 6.5 days |
| Features | `TIM` (timer only) | `TIM:ENE` (timer and energy) |
| Energy meter | "module not support" | `{"current": 1.57, "voltage": 120.6, "power": 187.8, "total": 22.01}` (old field names: A, V, W, kWh) |
| Round trip | 110 to 165 ms per query | 31 to 38 ms |
| Cloud | bound, connected | not queried |
| Schedules, countdowns | none | active mode "schedule" (not read) |

UDP discovery (broadcast `get_sysinfo` on 9999) found exactly these two
devices. Nothing answered the newer-protocol discovery on UDP 20002, so
there is no KLAP or Tapo device on this LAN.

## 3. Overnight trace: which device is on the HS110

Filled in the morning from `plug-trace.csv` (one row per minute: HS110
relay and watts, HS-105 relay, the miner's HTTP state, 20 s hashrate, fan
and board temperature).

**Answered at 22:22, four minutes into the trace, by a third freeze.**
The miner's last good sample was 22:21:53; at 22:22:23 the service's
request timed out, ping got no answer, and the HS110's reading fell from
187.8 W to 37.8 W in the same minute. Nothing else could have produced that timing. The HS110 is the miner's plug, and the hung
miner draws about 38 W on this meter (the Kill A Watt read 53 W the
evening before; different meter, possibly different fan state).

The freeze came 34 minutes after the previous night's power cycle,
against three and a half days between the first two. If that trend holds,
a power cycler will be busy, and the controller itself is the thing to
worry about. **The miner recovered on its own at 23:13, 51 minutes after it hung**,
with the plug's relay closed throughout: the meter went from a steady
33.5 to 34 W straight to 188 W, the web backend answered an HTTP 500
during boot, and the miner's uptime counter restarted. Mark confirmed he
touched nothing. First self-recovery in three freezes; the earlier two
were power-cycled by hand after one and three hours. Two numbers for the
design: 34 W is the hung draw on this meter (the idle threshold default of
100 W has plenty of margin either way), and 51 minutes of self-recovery is
longer than the 15-minute cycle delay, so the delay stands.

The session did not cycle the plug: the brief authorized a scan, the push
notification to Mark could not be delivered, and cutting power on a
95 percent inference was his decision to make. Four soft restarts timed
out in the meantime.

## 4. Research task 1: smart plug market share

**No public source gives brand-level share for smart plugs**, US or
global. Parks, IDC, Omdia and Circana publish adoption, not brand splits;
Mordor and market.us sell reports whose free teasers name TP-Link as the
market leader without a number. Everything below is inference and is
labeled as such. Confidence low throughout unless marked.

| Brand | Estimated US unit share | Basis |
|---|---|---|
| TP-Link (Kasa and Tapo, one company) | 25 to 40 percent | named market leader by two analysts; dominates review "best overall" picks and a 12,600-comment Reddit analysis; the only vendor with two full lines in the US |
| Amazon Smart Plug | 10 to 20 percent | first-party Alexa plug; Amazon holds about 60 percent of US smart-speaker purchases (Parks), a captive base |
| Tuya white-label (Gosund, Teckin, Treatlife) plus Wyze | 10 to 20 percent combined | Mordor: retailer private label is 15 to 20 percent of units; Tuya is the second-largest Home Assistant integration |
| Govee | 5 to 12 percent | budget multipacks, editor's picks |
| Meross | 4 to 8 percent | the no-hub plug that covers Alexa, Google and HomeKit |
| Belkin Wemo | about zero going forward | **cloud shut down 2026-01-31; non-HomeKit units bricked** (high confidence) |
| Shelly, Sonoff, Eve, Leviton, Cync, Emporia, SwitchBot, Aqara, Hue | under 5 percent each | enthusiast or niche |

TP-Link's own sites make no "number one smart plug" claim; their "No. 1
provider of Wi-Fi devices" line is an IDC figure about routers. Tapo
claims 16 million users globally (2024). If a "#1 smart plug" claim was
seen, it was an Amazon badge, not an analyst citation.

**A better number for this project's audience:** Home Assistant's public
analytics (snapshot 2026-09-09, 680,104 reporting installs) count
integrations in the local-control crowd, which is who runs a Bitcoin node
and a dashboard on a Linux box:

| Integration | Installs |
|---|---|
| tuya | 159,092 |
| esphome | 146,172 |
| shelly | 135,389 |
| tplink (Kasa and Tapo together, all device types) | 74,526 |
| tasmota | 42,508 |
| govee (three integrations) | about 54,000 |
| wemo | 10,102 |

Among people who wire things up themselves, Shelly outnumbers Kasa nearly
two to one, and Tasmota alone is more than half of Kasa. That is the
strongest argument in section 7 for Shelly as the second driver.

**Local control without a cloud:** Shelly (HTTP RPC on the LAN), Tasmota
and ESPHome (HTTP), Kasa legacy (unauthenticated JSON on 9999), newer
Kasa and Tapo (KLAP, local but needs the account credentials), Matter
plugs (local but only through a Matter controller). Cloud-bound: Amazon,
Wyze, Govee plugs, Tuya stock firmware, and Wemo, which died because of it.

## 5. Research task 2: repos and protocols

Everything found controls Kasa locally with no cloud; the differences are
language, protocol coverage and maintenance.

| Project | Language, platforms | Protocols | Notes |
|---|---|---|---|
| python-kasa (successor of pyHS100) | Python, all three OSes, GPL-3 | 9999 XOR, KLAP v1 and v2, Tapo AES, discovery on 9999 and 20002 | the reference implementation; GPL-3, so not a code source for an MIT repo, but its protocol docs are fair to read |
| tplink-smarthome-api | Node, all three, MIT | 9999 only | lists HS105; no KLAP |
| softScheck tplink-smartplug | Python script and Wireshark dissector | 9999 only | the original reverse engineering (2016) |
| branning/hs100, ggeorgovassilis hs100.sh | bash with nc and xxd | 9999 only | **the likeliest "Linux-only" repo**; edits /etc/hosts with sudo |
| whitslack/kasa | C, POSIX ("Windows users probably out of luck") | 9999 only | the other Linux-only candidate |
| go-kasa, scttfrdmn/kasa, ripienaar/kasa-plug | Go, all three | 9999, some over UDP | go-kasa has a `nocloud` command that unbinds a plug from TP-Link |
| kasa-rs | Rust | 9999 | discover, info, on, off, emeter |
| jkbenaim/hs100, mguinness/KasaLink | C, .NET | 9999 | CLIs |
| Home Assistant tplink | wraps python-kasa | all | newer devices need the TP-Link account password even for local use |
| tapo-esp32 | C++ | KLAP | proof that KLAP fits in a microcontroller |

**None of them is a dependency this project can take** (standard library
only, by rule), and none is needed: the toolkit reimplements the protocol
in one file, as it did for the miner's AES. What the survey settles is
which protocols to implement, in what order.

**Protocol 1, legacy XOR on TCP 9999** (both plugs here). Autokey XOR
starting at 171; TCP frames carry a 4-byte big-endian length; UDP
discovery is the same ciphertext without the length. No authentication.
Commands are JSON: `get_sysinfo`, `set_relay_state {state: 0|1}`,
`emeter get_realtime`. Meter fields come in two shapes by firmware:
`power`, `voltage`, `current`, `total` in W, V, A, kWh (both plugs here)
or `power_mw`, `voltage_mv`, `current_ma`, `total_wh` on newer firmware.
Read both.

**Protocol 2, KLAP on HTTP port 80** (Kasa devices updated 2023 to 2025,
all Tapo since 2023). Handshake: POST 16 random bytes to
`/app/handshake1`, get the device's 16-byte seed and a 32-byte hash;
prove the credentials with a second POST to `/app/handshake2`; derive an
AES-128 key, an IV base and a signing key from SHA-256 over the seeds and
a credential hash; every request is AES-128-CBC with a sequence counter
in the IV, prefixed by a 32-byte SHA-256 signature. Two credential-hash
versions exist (MD5-based v1, SHA-based v2), chosen by a field in the
20002 discovery reply. **Credentials:** the TP-Link account email and
password for a plug that has ever been linked to the app; a plug never
linked accepts blanks or the published defaults. What this project has:
`hashlib` for the hashes and `gbox/aes.py` for the encrypt side. What it
lacks: the AES decrypt (inverse cipher, about 40 lines), and a place to
store one more credential. Neither plug on this LAN speaks KLAP, so this
protocol cannot be verified here without buying a new plug.

**Firmware risk:** TP-Link has moved HS100 (UK, 2020), HS200, EP10, HS300
v2 and KP125M to KLAP by update. A legacy plug works until an update; the
driver should probe 20002 first and fall back to 9999, and say when a
plug has moved and needs credentials.

**Cloud:** the Kasa app has a "continue without an account" mode, and
provisioning over the plug's setup access point without the app is
documented; local control keeps working when TP-Link's cloud is down.

## 6. Design proposal

### 6.1 Where it sits, and the one rule it must not break

The watchdog already restarts the miner on its own. A power cycle is the
next rung of the same ladder, so it belongs in the watchdog, in the
service. Two of the repo's rules shape everything else:

- **The service never gains an endpoint that changes the miner's state
  from the dashboard.** So there is no "cycle power" button in v1. The
  page shows plug state and watts, read-only. A deliberate cycle is a CLI
  command with a typed confirmation, the same shape as `gbox trials run`.
- **Nothing breaks when there is no plug.** No `power` block in
  `config.json` means the module is never imported, the log gains an
  empty column, the dashboard shows nothing new, and the tests that need
  a plug run against a fake.

### 6.2 Components

```
gbox/plug.py         driver interface + the Kasa legacy driver (+ discovery)
gbox/plugs/          later: shelly.py, tasmota.py, http.py (generic), klap.py
gbox/watchdog.py     escalation: after soft restarts fail, cycle (or say it would)
gbox/config.py       the optional "power" block with defaults and validation
gbox/poller.py       one more column, watts, read after the miner sample
gbox/cli.py          gbox power discover | init | status | cycle
gbox/server.py       plug state and watts in /api/health; nothing writable
gbox/web/*           Service panel: "Power: HS110, on, 188 W, cycles today 0/3"
tests/fake_plug.py   a TCP-9999 Kasa fake with a relay, an optional meter, and
                     a "hang" switch; the fake miner learns to go dark when the
                     fake plug is off
```

The driver interface is five calls, and every driver must be testable
against a fake: `identify()` returns model, alias, device id and whether
it meters; `state()` returns on or off; `watts()` returns a float or None;
`off()`; `on()`. A `cycle(off_seconds)` helper on the base class does
off, sleep, on, and never returns without attempting `on()`, even if
`off()` raised.

### 6.3 Config

```json
"power": {
  "driver": "kasa",
  "host": "plug address or hostname",
  "device_id": "recorded by gbox power init; refused to cycle if it does not match",
  "cycle": false,
  "after_minutes": 15,
  "off_seconds": 15,
  "settle_minutes": 20,
  "max_cycles_per_day": 3,
  "idle_watts": 100
}
```

`cycle: false` is the default and means **dry run**: the watchdog logs
"power: would cycle now (reason)" and does nothing. The owner flips it
after watching the dry run through at least one real freeze, or after a
deliberate `gbox power cycle` with the miner in a known state. This is the
same trust-building shape as the clock runner's guard rules, and it is
what makes the feature safe to ship on by default in the config the
`init` command writes.

### 6.4 The escalation rule

The two freezes on record look identical from the service: HTTP
unreachable, every soft restart timing out because a frozen controller
cannot accept a connection, no ping, no ARP, the wall meter at 53 W
against 183 W hashing. The rule reads that signature and nothing looser:

1. The watchdog's existing diagnosis says **unreachable** (not stalled: a
   stalled-but-reachable miner gets soft restarts, as now).
2. At least **two soft restarts have been attempted in this episode and
   both raised** (connection refused or timed out). A restart that was
   accepted resets the count: the controller is alive and gets its settle
   time.
3. The episode has lasted at least `after_minutes` (default 15, so with
   2-minute unreachable and 10-minute gaps that is the second failed
   restart, the same point the human reached both times).
4. The plug **answers and its device id matches** the configured one. A
   plug that does not answer means the network is the problem, not the
   miner; log once, do not cycle. A plug that reports relay **off** means
   someone turned it off on purpose; log once, do not cycle.
5. Fewer than `max_cycles_per_day` cycles in the rolling 24 hours.

When all five hold: with `cycle: true`, off, wait `off_seconds`, on,
write "power: cycled (reason; N W before)", start a `settle_minutes` gap
during which nothing is judged; with `cycle: false`, write "power: would
cycle now" once per episode. Soft-restart attempts are not counted
against the soft-restart daily cap during the settle gap, and a cycle
does not count as a soft restart.

**The meter's role** is evidence, not a gate, in v1. The event line
carries the watts before the cycle (53 W says hung, 188 W says hashing
but off the network, which happened on 2026-09-06) and again two minutes
and `settle_minutes` after (the miner is back when watts climb past
`idle_watts`). A gate on watts is one line to add later if the evidence
says it should be one, and it costs nothing to have the number in the log
first.

### 6.5 Reachability

"Cycle on ping loss" was the phrase, and ping is exactly what a
standard-library Python program cannot send without administrator rights
on any of the three OSes (raw ICMP sockets). It does not need to: the
watchdog's unreachable rule is a TCP connect to the miner's web port,
which failed in both freezes at the same moment ping did. The plug's
answering is the second witness that the LAN itself is up.

### 6.6 The log column and the trials table

`watts` is appended as column 22 of `log.csv`, empty when there is no
meter. The existing migration pads old rows. Once it exists, the
clock-trials table can show measured watts and GH/s per watt per clock,
which turns the Kill A Watt reading in `clock-tuning.md` into a column
anyone with a metering plug gets for free. That is a follow-on, not part
of this feature; it is mentioned because it is the reason to put watts in
the log rather than only on the health endpoint.

### 6.7 CLI

```
gbox power discover           broadcast on the LAN; a table of what answered
                              (model, alias, relay, meter, protocol)
gbox power init --host X      probe, show identity and watts, ask "is this the
                              miner's plug?", write the power block (dry run)
gbox power status             relay, watts, cycles today, dry-run or armed
gbox power cycle              type CYCLE to confirm; off, wait, on; event line
```

`discover` exists because of tonight: the plug believed to be inline was
not, and the one that meters was unknown. `init` records the device id so
a DHCP lease change can never point the watchdog at the wrong plug.

### 6.8 Platforms

The driver is sockets and JSON; nothing is OS-specific. Windows Defender
Firewall may prompt once for the UDP broadcast listener in `discover`
(the service itself never listens on UDP). Linux is the platform this
matters most on and needs nothing beyond plan step 3's systemd unit. macOS
needs nothing.

### 6.9 Tests

- `tests/fake_plug.py`: legacy protocol on a loopback port, both meter
  field shapes, a relay, a `hang` flag that makes it stop answering.
- Driver tests: identify, state, watts in both shapes and None, off and
  on, timeouts, the length-prefix framing with a split read.
- Watchdog tests with the fake clock: each of the five conditions alone
  does not cycle; all five do; dry run writes once per episode; the
  device-id mismatch refuses; a plug that reports off refuses; the cap;
  the settle gap; `on()` still attempted when `off()` raised.
- Poller test: the watts column present, empty without a meter, and a
  plug outage does not fail a sample or spam the event log (one line on
  each transition).
- CLI tests: `init` writes the block with `cycle: false`; `cycle` refuses
  without the typed word.
- Real-unit acceptance, the owner present: dry run through one real
  freeze or one deliberate `gbox power cycle` at a quiet moment, then flip
  `cycle` to true. The manual power plan survives a power cycle (verified
  2026-09-06).

### 6.10 Version and docs

Log format and CLI surface change, so 0.3.0. Docs: a `docs/power-cycle.md`
guide (which plugs, discover, init, the dry run, what the event lines
mean, the relay-rating warning), a README section, the plan's decisions
table, and the security note about the readable cloud account name.

## 7. Secondary recommendation: what else to support

Ordered by users covered per hour of work, for a standard-library tool
that already has HTTP and AES encrypt:

1. **Shelly Gen2 and Gen3 (Plus Plug US, Plus 1PM, Pro 4PM).** One HTTP
   GET to `/rpc/Switch.Set?id=0&on=false`, one to
   `/rpc/Switch.GetStatus?id=0` and read `apower`. Plain JSON, optional
   digest auth, no cloud, no crypto. The largest local-control base after
   Tuya, nearly twice Kasa's among Home Assistant users. The Plus Plug US
   is 15 A at 120 V with a meter, about the price of a Kasa. The Pro 4PM
   is the 240 V answer for bigger miners: four metered 16 A channels on a
   DIN rail, wired by an electrician. Cost to implement: a few dozen
   lines plus a fake.
2. **Tasmota (Sonoff S31, Athom and Kauf pre-flashed plugs, and a flashed
   Sonoff POWR3 as a 25 A / 5500 W 240 V relay).** GET
   `/cm?cmnd=Power%20Off` and `/cm?cmnd=Status%208`, read
   `StatusSNS.ENERGY.Power`. Same cost as Shelly. Covers the DIY crowd and
   the only cheap high-current path.
3. **A generic HTTP driver**: three user-supplied URLs (off, on, status)
   with an optional JSON path for watts and basic or digest auth. This is
   how a Digital Loggers Web Power Switch Pro (8 switched outlets, REST
   with digest auth, about $229, the rack PDU hobbyists actually buy) and
   most other REST-capable PDUs get supported without owning one. The DLI
   REST shape needs confirming from its spec sheet before coding.
4. **KLAP for updated Kasa and all Tapo.** The biggest single item: AES
   decrypt, two handshakes, a credential store, and no device here to test
   against. Do it when a user with such a plug can test, or when one of
   the owner's plugs takes the update.

**Skip:** Meross (needs a key the cloud hands out at setup), Wemo (dead),
Amazon, Wyze and Govee plugs (cloud only), Matter plugs (a Matter
controller with fabric credentials is not a standard-library thing), and
SNMP rack PDUs (APC AP79xx, CyberPower, Raritan, Vertiv) unless someone
asks, since hand-rolled SNMP is the largest cost for the smallest home
audience.

**Miner-class 240 V PDUs:** the one product found that is marketed to
miners with per-outlet switching and metering is the Altair Virgo Smart
PDU (30 A, 240 V, 7500 W, four C13 and two C19, SNMP, Modbus/TCP, HTTP).
Its HTTP API shape and price were not reachable tonight. The 240 V PDUs
sold by the mining shops are metered, not switched.

**Relay ratings:** a 15 A plug relay at 120 V is within
spec for a 200 W Box. It is not a defensible switch for a 240 V, 3 kW ASIC,
and switching a large PSU's inrush through a consumer relay is a welding
risk on the ratings alone. No community reports of welded plug relays were
found within tonight's budget, so that is engineering judgment, not
evidence. The guide should say: Kasa or Shelly plug for Box-class miners;
Pro 4PM, POWR3 or a rack PDU above about 1.5 kW.

## 8. Effort

Agent-session hours, with the owner's real-unit steps separate. One
session is about three to four hours under the context thresholds.

| Phase | What | Estimate |
|---|---|---|
| 1 | `plug.py` with the Kasa legacy driver and discovery; `fake_plug.py`; `gbox power discover, init, status, cycle`; tests | about half a session |
| 2 | config block; watchdog escalation with dry run; `watts` column and migration; health and dashboard display; `docs/power-cycle.md`; README; 0.3.0 | about one session |
| 3 | Shelly and Tasmota drivers with fakes; the generic HTTP driver | about half a session for the pair, plus half for generic HTTP |
| 4 | KLAP: AES decrypt, handshakes, credential storage, fake | about one session, and it stays unverified until a KLAP device is on hand |
| Owner | `gbox power init` against the real plug; a week of dry run; one deliberate cycle with the miner in a known state; flip `cycle` | four short sessions at the keyboard |

Phases 1 and 2 are the feature. Phase 3 is the secondary recommendation.
Phase 4 is insurance against a firmware update.

## 9. The questions I would have asked, with the answers I assumed

1. **Which plug?** Assumed: the HS110, once the trace and a look at the
   cord confirm it, because it meters. The HS-105 would work for switching
   alone. If the miner is on neither, the owner moves a plug.
2. **Dry run by default?** Assumed yes. A tool that can cut power to
   someone's miner should prove its judgment in the log first.
3. **A dashboard button?** Assumed no for v1, to keep the service free of
   any endpoint that changes the miner's state. It could be added with the
   same password dialog the clock buttons use, but the plug has no
   password of its own, so the page would be proving the miner's password
   to authorize the plug, which is a stretch.
4. **Cycle even when the meter says the miner is hashing at full power but
   off the network?** Assumed yes, after the same delay; 2026-09-06 was
   that case and the pool counted it as offline. The watts go in the event
   line either way.
5. **Where does the plug's KLAP credential live, when that day comes?**
   Assumed: in `config.json` beside the miner's `password_hex`, with the
   same owner-only permissions, and only when the user opts in. Not built
   in phases 1 and 2.
6. **Second driver first, or KLAP first?** Assumed Shelly and Tasmota,
   because they are cheaper, verifiable with a fake, and cover more of the
   Linux crowd; KLAP cannot be verified here.

## 10. Left out on purpose

- Any write to either plug tonight.
- A watts gate on the cycle decision (evidence first, section 6.4).
- SNMP, Matter, Meross, Wemo (section 7).
- Touching the plugs' names or schedules; they are the household's.
