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

## `sc5proii/`: captured, a friend's unit

Captured 2026-09-15 from a friend's SC5 Pro II (hardware 30.50.SA, MCB_V3_3,
firmware 2.2.0). Adds the `[PGAn]` multi-board form of `/dbg/minerinfo` and
the port-4028 `devs`/`summary` JSON alongside the usual `/mcb/` and `/cpb/`
captures. See `sc5proii/README.md` for the file-by-file detail.

## `sclite/`: captured, MaVeTh's unit

Captured 2026-09-22 from MaVeTh's SC Lite (hardware 30.40.SA,
MCB_V4_3, firmware 2.2.0) per `docs/capture-request.md`, and published in
Maveth/goldshell-config under `sc-lite/webui/fixtures/sclite-live`. They
replace the synthetic files built 2026-09-13 from his notes. Four `[PGAn]`
boards, four fans, `/dbg/minerinfo` and `/dbg/icinfo` answering 200 (4 x 46
chips), port-4028 `devs`/`summary`, no `temp_targets` in `/mcb/setting`.
Sanitized by him: `name` (the unit's MAC) replaced with `00:11:22:33:44:55`;
no pools, syslogs, password or token. His `dbg_fanctrllog.txt` (740 KB) is
not copied: no test reads it, and its findings are in `docs/firmware-api.md`.

## `hsbox_synthetic/`: invented, not captured

Made up on 2026-11-01 for `tests/test_hs_probe.py`; nothing in it came from a
real unit. It is HS-BOX-shaped (firmware 2.2.6, MCB_V5_4, nested `powerplans`)
and holds one planted secret of each kind so the probe's redaction can be
tested: pool user `FAKEWALLETabc123xyz`, pool password `fakepass9`, SSID
`FakeSSID-77`, WiFi key `fakewifikey55`, and the MAC `02:1A:2b:3C:4d:5E`
(colon form in `dbg_minerinfo.txt`, dash form in `dbg_syslog.txt`).
`mcb_pools.json` also has a one-character user and password (`x`) to test the
short-string rule. Port-4028 replies are `api4028_<command>.json` for
`version`, `summary`, `devs`, `stats` (malformed `}{`, like the SC-BOX) and
`pools` (same user). The fake serves them only with `FakeMiner(study=True)`;
see that class for the extra routes (`/mcb/pools`, `/mcb/wifisetting`,
`/dbg/syslog`; 500 for `/mcb/cgminer` and `/dbg/vsinfo`; 404 for GET
`/mcb/protect` and `/mcb/defend`, whose real GET behavior is unknown).

The two synthetic logs (`dbg_syslog.txt`, `dbg_minersyslog.txt`) also carry echo shapes the probe must
drop whole (single-quoted, `key=value`, `wifipwd`/`pool_pass`, double-escaped, escaped quote inside a
value, the admin password in upper case on a line with no drop word) and lines that must survive
(`Minerd start !!!`, `Check Token Error`, `Restart Miner`, a temp/fan/clock line). `mcb_status.json`
carries a 12-digit `hashrate` and a 12-hex `ver` that the retired bare-hex MAC rule used to blank.
