---
title: Goldshell BOX firmware HTTP API
---

# Goldshell BOX firmware HTTP API

The web backend on port 80 of a Goldshell BOX (web UI titled "cloud-box").
Paths, login, the token, and the errors you will see. For the model and
firmware behind each tag, see the [matrix](README.md#model-and-firmware-matrix).
Evidence: [notebook, Authentication and Endpoints](../firmware-api.md#authentication).

## Login

`GET /user/login?username=admin&password=<hex>&cipher=true`

- `<hex>` is AES-128-CBC of the UTF-8 password, key `!!!!!!!!!!!!!!!!`
  (sixteen `!`), zero IV, zero padding (not PKCS#7), ciphertext as hex. WebCrypto
  only does PKCS#7: encrypt the zero-padded text and drop the final block.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- Response: `{"JWT Token": "<RS256 JWT>"}`.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- Every other request carries `Authorization: Bearer <token>`.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`. On the SC Lite both
  `Authorization: <token>` and `Authorization: Bearer <token>` worked on
  `/dbg/minerinfo`. `[reported: SC Lite owner, MCB_V4_3, fw 2.2.0, 2026-09-22]`

## The token never expires and is the same on every unit

- The payload has no expiry and no nonce, so every login returns the same
  token. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- The SC-BOX's token matched an SC5 Pro II's on all 458 compared characters
  (audience "yotta mc", issuer intchains.com, id 1, subject "minerd").
  The SC-BOX side is `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-15]`. The
  SC5 Pro II side is another owner's capture:
  `[reported: SC5 Pro II owner, MCB_V3_3, fw 2.2.0, 2026-09-15]`.
- So the web password gates nothing the miner does over HTTP. Anyone on the
  network holding any Goldshell's token can read settings, change pools or
  restart the unit. Public helper repos carry that token. This is
  `[unverified]` as a general claim for every model: it follows from the two
  units compared.
- Changing the password does not invalidate the token. An earlier security note
  said it was the only way; the payload is fixed and RS256 is deterministic, and
  the same token worked on two units. `[contradicted: 2026-09-15, see notebook
  Authentication]`. No way to invalidate it is on record.
  ([evidence](../firmware-api.md#authentication))

## Logout, reboot and `Check Token Error`

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-29]`, from the miner's own web
log (`/dbg/syslog`).

- `GET /user/logout` (what the factory page's "Lock" sends) and a reboot both
  make the miner refuse the token to every client. Each request gets 401 with
  `Check Token Error` until anyone logs in successfully.
- After a successful login the same token works for every client again,
  including ones that never logged in. A 401 is not a reason to discard the
  token: a new login returns the same string.
- The log records `Login`, `Logout` and `Check Token Error` lines, with no
  detail of why a login failed.

## Login fails with a bare HTTP 500

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-29]`

- The SC-BOX answered a script's login with a bare HTTP 500 (`Server Error`)
  for 63 minutes, about every 30 seconds, while logins from the factory page in
  a browser went through. The password, request headers, cookies and an
  `Authorization` header were ruled out. The cause is inside the firmware and
  unknown. It ended after logins from the factory page.
- A wrong password gets the same bare 500.
- What gbox does about it: it retries the login, but it cannot tell a wrong
  password from this fault.
  ([evidence](../firmware-api.md#authentication))

## CORS

- `Access-Control-Allow-Origin: *` on 200 answers, but not on the 500
  `Server Error` answers. A page on another origin cannot read the status of a
  failed login, and the browser reports only "Failed to fetch".
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-29]`
- A preflight `OPTIONS` on `/mcb/setting` and `/mcb/restart` answers 200 with
  `Access-Control-Allow-Methods: GET, PUT, POST, OPTIONS` and
  `Access-Control-Allow-Headers: appkey,X_forwarded-for,Content-Type,Authorization, X-Requested-With`.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]`
- Consequence: a web page opened from disk or another origin can PUT settings
  and restart the miner if it holds the token.

## Endpoint catalogue

Every path needs the token except `GET /mcb/status`.

| Path | Method | What it does | Tag |
|---|---|---|---|
| `/mcb/status` | GET | model, hardware, mcbversion, firmware; answers with no `Authorization` header | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-19]`; `[unverified]` on the SC5 Pro II |
| `/mcb/setting` | GET/PUT | the [settings object](settings-and-power-plans.md) | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |
| `/mcb/pools` | GET/PUT | pool list, with pool user and worker password in plain text | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]` |
| `/mcb/newpool`, `/mcb/delpool` | PUT | add or remove a pool | `[unverified]`, seen in the stock UI's API module |
| `/mcb/restart` | PUT, no body | soft restart; a full reboot of the controller | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |
| `/mcb/facrst` | PUT | factory reset; never call from tooling | never called |
| `/mcb/algosetting`, `/mcb/cmossetting`, `/mcb/rgbsetting`, `/mcb/ip`, `/mcb/wifisetting` | GET/PUT | assorted settings | `[unverified]` except WiFi below |
| `/mcb/cgminer` | GET | returned HTTP 500 | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |
| `/mcb/protect`, `/mcb/defend` | PUT, no body | in the stock UI's API module (2026-09-07); purpose unknown | never called |
| `/mcb/uploadimage` | POST | firmware upload | never called |
| `/mcb/tutorial`, `/mcb/resultpool`, `/mcb/wifiresult` | GET | stock UI helpers | `[unverified]` |
| `/cpb/hshistory` | GET | JSON array, 288 samples, one per minute, MH/s, newest last, zeros before the first sample | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |
| `/dbg/minerinfo` | GET | cgminer-style text: a `[STATUS]` block, then one `[PGAn] =>` block per hashboard | see below |
| `/dbg/icinfo` | GET | JSON `{body: "<json string>"}` with per-chip counts | see below |
| `/dbg/fanctrllog` | GET | fan daemon log, grows to about 1 MB | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |
| `/dbg/minersyslog` | GET | cgminer log as text | see [behavior](behavior-and-failure-modes.md#the-miner-log-dbgminersyslog) |
| `/dbg/syslog` | GET | web backend log: `Check Token Error`, `Minerd start !!!`, `Restart Miner` | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-10-29]` |
| `/dbg/kmsg`, `/dbg/meminfo`, `/dbg/psinfo`, `/dbg/monitorlog`, `/dbg/minerhistory` | GET | system diagnostics | `[unverified]`, listed by the debug page |
| `/dbg/vsinfo` | GET | returned HTTP 500 | `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]` |

([evidence](../firmware-api.md#endpoints-all-need-the-token-except-mcbstatus))

### `GET /mcb/status`, no token

Example answer:
`{"hardware":"40.40.HA","model":"Goldshell-SCBox","mcbversion":"MCB_V5_4","firmware":"2.2.5"}`.
`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-19]`. A wrong token on this
path is untested. gbox's `discover` probes with this request, so a sweep reads
and sends no credential.

### `GET /dbg/minerinfo`

- Layout: a `[STATUS]` block, then one `[PGAn] =>` block per hashboard: one on
  the SC-BOX, four on the SC5 Pro II and SC Lite.
- Keys used: `Device Elapsed`, `MHS av`, `MHS 20s`, `Accepted`, `Rejected`,
  `Hardware Errors`, `Device Hardware%`, `clock`, `fan0`, `fan1`, `tstemp-0`,
  `tstemp-2`, `rebootcnt`, `overheat`.
- `tstemp-0` is the board's chip average plus about 3 C on the SC-BOX, never
  the hottest chip. `tstemp-2` is the board sensor, the temperature the stock
  UI shows and the fan controller steers on.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-13]`
- The SC5 Pro II's `[STATUS]` block carries `voltage` (mV), `current` and
  `powerid`. `current` is unit-level, repeated on every board, and in mA only by
  arithmetic against the 3300 W rating (`[unverified]`).
  `[reported: SC5 Pro II owner, MCB_V3_3, fw 2.2.0, 2026-09-15]`
- The SC-BOX's block carries none of the three, so this transport yields no
  firmware watts figure on that unit.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-15]`
- Answered 200 on an SC5 Pro II with and without a Referer, so the "debug lock"
  that SC Lite notes describe is not on that firmware.
  `[reported: SC5 Pro II owner, MCB_V3_3, fw 2.2.0, 2026-09-15]`
- Answered 200 on an SC Lite, four `[PGAn]` blocks.
  `[reported: SC Lite owner, MCB_V4_3, fw 2.2.0, 2026-09-22]`
- A single 401 on a `/dbg/` request is the token race. Retry it.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`

### `GET /dbg/icinfo`

- `drawdata` is an array of boards, each an array of chips with `chipindex`,
  `perf` (good nonces) and `hwerr` (bad nonces). Chips are numbered from 1.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-05]`
- Every chip's `temp` reads 0.0, so no API field gives the hottest chip.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-13]`
- The counters restart at every board reinit, so per-chip rates are sums of
  increments between samples.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`
- `Hardware Errors` in `/dbg/minerinfo` equals the sum of `hwerr` over every
  chip. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-08]`

## WiFi credentials in `GET /mcb/wifisetting`

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]`, on a second-hand unit
that had been factory reset.

- The answer holds SSIDs and passwords in plain text, with `mode: "sta"`:
  `{"enable": false, "version": "v1.0", "mode": "sta", "network": [...], "exist": true}`.
- The `network` list cannot be changed through this endpoint. The PUT only
  starts or stops the WiFi station daemon according to `enable`; the list is
  ignored. Tested five ways, including a full enable-then-disable cycle.
- Factory reset did not clear the stored networks on that unit. If you sell a
  Box, assume its WiFi credentials travel with it; for every other unit this is
  `[unverified]`. The only remedy is to change the WiFi password on the router.
- The web backend echoes every PUT body, passwords included, into `/dbg/syslog`.
- The shipped web UI has no WiFi page: none of its four JavaScript chunks
  reference an SSID.

([evidence](../security-notes.md#the-miner-stores-your-wifi-credentials-in-plain-text))

## Other plain-text exposure

- `GET /mcb/pools` returns pool URL, worker name (often a wallet address) and
  worker password in plain text. `GET /mcb/setting` returns the unit's MAC in
  `name`. `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-06]`
  ([evidence](../security-notes.md#other-things-the-api-exposes-to-a-token-holder))
- Remove those fields before you paste any capture into a chat.

## How to see most of this without code

The stock UI has a hidden page, `/#/debug`, which renders most `/dbg/`
endpoints. Open it in the browser where you are logged in. It is heavy on the
miner: do not leave it auto-refreshing. See
[the debug page note](../stock-ui-debug-page.md).
`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-07]`. The MINER, CHIPS and
MINERSYSLOG tabs worked on an SC5 Pro II:
`[reported: SC5 Pro II owner, MCB_V3_3, fw 2.2.0, 2026-09]`.
