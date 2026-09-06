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
