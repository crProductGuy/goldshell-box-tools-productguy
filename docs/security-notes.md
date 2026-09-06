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
