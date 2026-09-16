# `sc5proii/`

Captured 2026-09-15 from a friend's SC5 Pro II (hardware 30.50.SA, MCB_V3_3,
firmware 2.2.0), one request at a time; `/dbg/minerinfo` answered 200 with
the token; port 4028 answered without one.

| File | Endpoint | Sanitized |
|---|---|---|
| `mcb_status.json` | `GET /mcb/status` | nothing to remove |
| `mcb_setting.json` | `GET /mcb/setting` | `name` (the unit's MAC) already replaced with `00:11:22:33:44:55` at capture time |
| `cgminer_devs.json` | `GET /mcb/cgminer?cgminercmd=devs` | nothing to remove |
| `mcb_algosetting.json` | `GET /mcb/algosetting` | nothing to remove |
| `cpb_hshistory.json` | `GET /cpb/hshistory` | nothing to remove |
| `dbg_minerinfo.txt` | `GET /dbg/minerinfo` | the `**RESPONSE**` body only; the capture's `**REQUEST**` section carried the bearer token and was never copied |
| `api4028_devs.json` | `{"command":"devs"}` on port 4028 | trailing NUL byte stripped |
| `api4028_summary.json` | `{"command":"summary"}` on port 4028 | trailing NUL byte stripped |

`/mcb/pools` is deliberately absent, as it is for every other model's
fixtures: it carries the pool URL, wallet and worker password.
