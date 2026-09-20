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
