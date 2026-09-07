# Goldshell Box firmware API notes

Learned on 2026-09-05 from an SC-BOX, hardware 40.40.HA, controller
MCB_V5_4, firmware 2.2.5 (web UI titled "cloud-box"). Other Box-series
models ship the same UI and are expected to match; confirmations welcome.
No Goldshell code is reproduced here, only observed behavior.

## Authentication

- `GET /user/login?username=admin&password=<hex>&cipher=true`
- `<hex>` is AES-128-CBC of the UTF-8 password, key `!!!!!!!!!!!!!!!!`
  (sixteen `!`), zero IV, **zero padding** (not PKCS#7), ciphertext as hex.
  WebCrypto only does PKCS#7: encrypt the zero-padded text and drop the final
  block, the CBC chain is identical up to there.
- Response: `{"JWT Token": "<RS256 JWT>"}`. The payload has no expiry and no
  nonce, so every login returns the identical token and it never expires.
  Treat the token as password-equivalent.
- Every other request: header `Authorization: Bearer <token>`.
- CORS: `Access-Control-Allow-Origin: *`. A preflight `OPTIONS` on
  `/mcb/setting` and `/mcb/restart` answers 200 with
  `Access-Control-Allow-Methods: GET, PUT, POST, OPTIONS` and
  `Access-Control-Allow-Headers: appkey,X_forwarded-for,Content-Type,Authorization, X-Requested-With`
  (checked 2026-09-06). So a page opened from disk or another origin can PUT
  settings and restart the miner, which is what the dashboard's buttons do.

## Endpoints (all need the token)

| Path | Method | Notes |
|---|---|---|
| `/mcb/status` | GET | model, hardware, mcbversion, firmware |
| `/mcb/setting` | GET/PUT | see Settings below |
| `/mcb/pools` | GET/PUT | pool list |
| `/mcb/newpool`, `/mcb/delpool` | PUT | |
| `/mcb/restart` | PUT, no body | soft restart (full reboot of the controller) |
| `/mcb/facrst` | PUT | factory reset, never call from tooling |
| `/mcb/algosetting`, `/mcb/cmossetting`, `/mcb/rgbsetting`, `/mcb/ip`, `/mcb/wifisetting` | GET/PUT | |
| `/mcb/cgminer` | GET | returned 500 on this unit |
| `/cpb/hshistory` | GET | JSON array, 288 samples, one per minute, MH/s, newest last, zeros before first sample |
| `/dbg/minerinfo` | GET | cgminer-style text: `[key] => value` lines. Keys used: `Device Elapsed`, `MHS av`, `MHS 20s`, `Accepted`, `Rejected`, `Hardware Errors`, `Device Hardware%`, `clock`, `fan0`, `fan1`, `tstemp-0` (chip temp), `tstemp-2` (board sensor), `rebootcnt`, `overheat` |
| `/dbg/icinfo` | GET | JSON `{body: "<json string>"}`; `drawdata` is an array of boards, each an array of chips with `chipindex`, `perf` (good nonces), `hwerr` (bad nonces) |
| `/dbg/fanctrllog` | GET | fan daemon log, grows to ~1 MB; lines like `Fans Change (fan0: 62 ==> 61) ... reason(t:64.2 acc:0.0 target_temp:65)` |
| `/dbg/minersyslog` | GET | cgminer log, truncates at ~3 MB |
| `/dbg/syslog` | GET | web backend log; `Check Token Error`, `Minerd start !!!`, `Restart Miner` live here |
| `/dbg/kmsg`, `/dbg/meminfo`, `/dbg/psinfo`, `/dbg/monitorlog`, `/dbg/minerhistory` | GET | |
| `/dbg/vsinfo` | GET | 500 on this unit |

The hidden page `/#/debug` in the stock UI renders most of the `/dbg/` data.

## Settings object

```json
{"select":0,"tempcontrol":true,"ledcontrol":true,
 "manualPowerplan":"725 MHz 0.41 V 70 RPM 70 RPM",
 "name":"<MAC>","powerplans":[{"level":0,"info":"725 MHz 0.41 V 70 RPM 70 RPM"},{"level":3,"info":"0 MHz 0 V 70 RPM 70 RPM"}],
 "manual":false,"temp_targets":[65.0,75.0],"version":"v2.1","temp_target":65}
```

- PUT the whole object back with changes. `manual:true` + a new
  `manualPowerplan` string changes the clock live, no reboot.
- **The fan fields in the plan string are ignored.** Fan speed is a PID on
  the board sensor (`tstemp-2`, the temperature the stock UI shows) toward
  `temp_target`. The board sensor reads 5-10 C below the chips.
- `temp_target` is clamped by the fan daemon to `temp_targets` (65-75 on this
  unit); `temp_targets` itself is read-only. 65 is the coolest available.
- `tempcontrol` is the overheat-shutdown flag; it does not affect fans.
- Each settings PUT restarts the fan daemon, which spikes the fans for a few
  minutes before the PID settles again. Do not read that spike as a result.
- The stock UI's manual power-plan field is dead code (its toggle is never
  set), so the GUI can only pick presets, and its Save handler clears
  `manual`. Tell users never to press Save on the stock Miner page.

## Reliability quirks

- The token check has a race: with several requests in flight, roughly one in
  200 gets `401` and the syslog shows `Check Token Error` preceded by a failed
  reload of `pubkey.pem`. Send requests one at a time; retry a 401 before
  believing it.
- Request bursts of roughly 15 per second crash the web backend (`Minerd
  start !!!` in syslog). It restarts on its own and loses the hashrate history
  buffer. Poll gently: one cycle per 10 s, slow endpoints once a minute.
- `/dbg/` files are regenerated per request; `dbg/fanctrllog` occasionally
  returns a length mismatch mid-write. Keep the last good value.

## Hashboard behavior seen on the SC-BOX

- 16 ICT580 chips on one board (`CPB0`). Healthy chips return good nonces at
  roughly equal rates with single-digit bad nonces.
- A marginal chip at the factory clock floods the board with bad nonces; the
  firmware answers `Read Nonce Faild 10 Times, Reinit Device!!!` and resets
  the whole board (clock ramp 50 -> 725 MHz, ~10 s of no hashing) every few
  seconds. `rebootcnt` in `minerinfo` counts these. Lowering the clock to
  600 MHz stopped it entirely on this unit.
- A second failure mode: the board accepts work and reports each job finished
  instantly without hashing; the firmware notices after minutes via
  `SEND JOB FAILD 10 TIMES, REINIT THIS CPB`.
