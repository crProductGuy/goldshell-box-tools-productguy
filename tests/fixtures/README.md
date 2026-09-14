# Fixtures

Sanitized captures from a Goldshell SC-BOX (hardware 40.40.HA, MCB_V5_4,
firmware 2.2.5) taken 2026-09-05, one request at a time.

| File | Endpoint | Sanitized |
|---|---|---|
| `mcb_status.json` | `GET /mcb/status` | nothing to remove |
| `mcb_setting.json` | `GET /mcb/setting` | `name` (the unit's MAC) replaced with `00:11:22:33:44:55` |
| `dbg_minerinfo.txt` | `GET /dbg/minerinfo` | nothing to remove |
| `dbg_icinfo.json` | `GET /dbg/icinfo` | nothing to remove |
| `cpb_hshistory.json` | `GET /cpb/hshistory` | nothing to remove |

`/mcb/pools` is deliberately absent: it carries the pool URL, wallet and
worker password.

## `sclite/`: synthetic, not captured

Built 2026-09-13 from the other developer's notes on an SC Lite, firmware
2.2.0 (Maveth/goldshell-config, read 2026-09-12): the `model` string, the
power plan dialect (`<MHz> MHz <mV> V <fanA> RPM <fanB> RPM PV <pv>`), and
the absence of a fan-target range. `hardware` and `mcbversion` are
`unknown` because no capture has shown them. These files exercise the
model seam; a real capture (`docs/capture-request.md`) replaces them.
