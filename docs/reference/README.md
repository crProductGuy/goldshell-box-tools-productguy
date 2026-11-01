---
title: Goldshell BOX controller and API reference
---

# Goldshell BOX controller and API reference

How the controller in a Goldshell BOX-series miner (SC-BOX, HS-BOX, and the
related SC5 Pro II and SC Lite) answers over HTTP and on port 4028, what its
settings really do, and how it fails. It is about the miners. You do not need
gbox to use it. gbox appears only as "how this was found" and "what gbox does
about it".

Nothing here reproduces Goldshell code. It is observed behaviour, with the
evidence in [the notebook](../firmware-api.md), which this reference
summarises and links to section by section.

## Read this first: safety rules

Each rule was learned the hard way. Follow them before you script anything.

1. **One request in flight at a time.** The token check races: with several
   requests in flight, about one in 200 gets `401` and the miner's web log
   writes `Check Token Error`.
   `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`
   ([evidence](../firmware-api.md#reliability-quirks))
2. **Never send about 15 requests per second.** That crashes the web backend
   (`Minerd start !!!` in `/dbg/syslog`), and it loses the hashrate history
   buffer. It restarts by itself. Poll one cycle per 10 seconds at most, and
   slow endpoints once a minute.
   `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`
   ([evidence](../firmware-api.md#reliability-quirks))
3. **Never call `PUT /mcb/facrst`.** It is a factory reset, one request away
   once you hold a token. It does not clear stored WiFi credentials either.
   `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]`, never called, so the
   reset itself is `[unverified]`.
   ([evidence](../firmware-api.md#endpoints-all-need-the-token-except-mcbstatus))
4. **Treat the miner as an unauthenticated device.** The login token never
   expires and is identical on every unit. Keep the miner on a LAN you trust
   and never port-forward it. ([details](firmware-http-api.md#login))
5. **Never save from the stock Miner page while you run a tuned clock.** Every
   Save writes `manual:false` and drops the unit back to the preset.
   ([details](settings-and-power-plans.md#what-the-stock-ui-save-writes))
6. **Do not read or store `/dbg/minersyslog` carelessly.** It repeats your
   pool user, which is usually a wallet address. Screenshots of the debug page
   carry it too. ([details](behaviour-and-failure-modes.md#the-miner-log-dbgminersyslog))
7. **A soft restart is not free.** Of four sent while the board was present,
   three lost the board. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-20]`
   ([evidence](../firmware-api.md#two-ways-a-sick-board-looks-and-they-behave-oppositely))

## The tags

Every fact in this reference carries one tag, so a 2026 page stays usable when
a firmware update changes one cell.

| Tag | Meaning |
|---|---|
| `[verified: model, controller, firmware, date]` | read or tested on a unit by someone with the unit in front of them |
| `[reported: source]` | someone else's capture or notes, not re-tested here |
| `[unverified]` | inferred, or described in a source with no test behind it |
| `[contradicted: date, see ...]` | an earlier claim that a later observation disagrees with; kept so you can see the history |

When you answer a question from this page, say which firmware the tag names.
"Verified on fw 2.2.5 only" is the right level of confidence.

## Model and firmware matrix

Columns are the units on record. Cell codes: **V** verified on a unit,
**R** reported by another owner, **U** untested on that model, **F** fails
(returns an error on that model). Dates are in the page for each row.

| Endpoint or setting | SC-BOX (MCB_V5_4, fw 2.2.5) | HS-BOX (MCB_V5_4, fw 2.2.6) | SC5 Pro II (MCB_V3_3, fw 2.2.0) | SC Lite (MCB_V4_3, fw 2.2.0) |
|---|---|---|---|---|
| `GET /mcb/status` with no token | V | U | U | U |
| `GET /user/login` (cipher login) | V | U | V | U |
| `GET /mcb/setting` | V | V | U | R |
| `PUT /mcb/setting` (clock, fan target) | V | U | U | R |
| `GET /mcb/pools` | V | U | U | U |
| `PUT /mcb/restart` | V | U | U | U |
| `PUT /mcb/facrst` | never called | never called | never called | never called |
| `GET /mcb/cgminer` | F (500) | U | U | U |
| `GET/PUT /mcb/wifisetting` | V | U | U | U |
| `GET /cpb/hshistory` | V | U | U | U |
| `GET /dbg/minerinfo` | V | U | V | R |
| `GET /dbg/icinfo` | V | U | U | U |
| `GET /dbg/fanctrllog` | V | U | U | R |
| `GET /dbg/minersyslog` | V | U | R | U |
| `GET /dbg/syslog` | V | U | U | U |
| `GET /dbg/vsinfo` | F (500) | U | U | U |
| Port 4028 `devs` | V | U | V | R |
| Port 4028 `summary` | V | U | U | U |
| Port 4028 `stats` (malformed JSON) | V | U | U | U |
| `manual` and `manualPowerplan` fields | V | V | U | R |
| Flat `powerplans` list | V | F (nested) | U | R |
| `tempcontrol` has no effect on fans | U | U | U | R |
| Voltage on 4028 is a setpoint echo | V | U | F (millivolts) | F (not the setpoint) |
| Posts to `find.goldshell.com` | V | U | U | U |

Where "F" is not an error code but a difference: the HS-BOX has a nested
`powerplans` shape, and the SC5 Pro II and SC Lite report a different voltage
unit.

## The pages

- [HTTP API](firmware-http-api.md): login, token behaviour, the endpoint table.
- [Settings and power plans](settings-and-power-plans.md): `/mcb/setting`,
  `manual`, `manualPowerplan`, the plan dialects, what each stock-UI Save writes.
- [Port 4028](port-4028.md): the cgminer socket, no token.
- [Behaviour and failure modes](behaviour-and-failure-modes.md): counters,
  self-restarts, how a sick board looks, fans, the miner log.
- [Off-LAN traffic](off-lan-traffic.md): what the firmware sends to
  `find.goldshell.com`.

The evidence for all of it, in the order it was learned, is
[the notebook](../firmware-api.md).

## Open questions

These are contradictions or gaps in the source documents. They are listed, not
resolved.

1. **Does the manual power plan survive a power cycle?** Older notes said yes.
   `[contradicted: 2026-11-01, see settings-and-power-plans.md]`. The plan
   survived more than 20 cold boots between 2026-10-20 and 2026-10-31, then
   came back as the preset on 2026-11-01. One observation, cause unknown.
2. **What zone does the miner's log clock use?** The notebook says the
   SC-BOX's log ran 12 hours ahead of local time in September 2026. A later
   reading says UTC+8 on the same family. Whether the unit changed or the two
   readings used different local zones is not settled.
   ([details](behaviour-and-failure-modes.md#the-miners-clock))
3. **Are the HS-BOX facts from one unit?** Everything about the HS-BOX is one
   unit read on 2026-09-25. Treat it as one sample.
4. **Do other models post to `find.goldshell.com`?** Only the SC-BOX was
   checked. ([details](off-lan-traffic.md))
5. **What does `tempcontrol:false` do under heat?** Untested on any model.
6. **What do `/mcb/protect` and `/mcb/defend` do?** Never called.
