# Remote console access and the multi-dashboard guard (0.11.0)

Design agreed 2026-09-23 with the owner, in three approved sections. Target version 0.11.0.

## Goal

The owner's Mac becomes the **full remote management console** for the gbox service: everything the
dashboard does on the service's own machine, including the password-protected buttons, from another machine
on the **same LAN**. The service may move from the Windows PC to a small Linux box (a Raspberry Pi), so the
design must work on both without change.

Success: the owner opens a browser on the Mac, sees the live dashboard and can use every control; no other
device on the LAN gains any access it does not have today; two dashboards open at once never double the load
on the miner.

Out of scope: access from outside the LAN; a login inside gbox; the miner's own web UI through the tunnel
(approach B, gated on test E4 below); making the service the only reader of the miner (belongs with miner
network isolation); multi-miner support beyond running one service per miner.

## Approaches considered

| | Approach | Decision |
|---|---|---|
| A | SSH tunnel to the service; the page still reaches the miner directly | **Chosen now** |
| B | A, plus a forward to the miner's web UI, so all console traffic rides the tunnel | **Later**: worth it once the miner is isolated on its own network, and only if the stock UI works as `localhost:8080` (E4) |
| C | A login inside gbox and `--bind` to the LAN | **Rejected**: new authentication code in the service that controls the plug, plain HTTP on the LAN unless TLS is added, and a password form instead of an SSH key as the barrier |

## Part 1: access path

The service is unchanged and stays bound to `127.0.0.1`. The Mac reaches it through SSH local forwarding.
A tunnelled request arrives as `localhost`, so the 0.10.1 Host check passes it. The page fills in the miner
address from `/api/health` (`app.js`, `probeService`) and then talks to the miner directly over the LAN, as it
does on the service's own machine. The Mac browser keeps its own miner login because `localhost:8765` on the
Mac is its own origin.

**Host side.** sshd with key-only login, password login off, and a firewall rule admitting only the Mac's
reserved LAN addresses. On Windows this is the existing SSH-star setup script (run elevated, dry-run first);
on Linux, `sshd_config` and the host firewall. Two keys, each on its own line:

- the **gbox key**, tunnel-only:
  `restrict,port-forwarding,permitopen="127.0.0.1:8765",permitopen="127.0.0.1:8766" ssh-ed25519 ...`
- a **shell key** for general administration, on a separate line, unrestricted.

On Windows, an Administrator's keys are read from `C:\ProgramData\ssh\administrators_authorized_keys`, not
from the user's own `~/.ssh/authorized_keys`. A leaked gbox key opens the dashboard and nothing else.

**Mac side.**

```
Host gbox
  HostName <service machine>
  User <user>
  IdentityFile ~/.ssh/<gbox key>
  IdentitiesOnly yes
  LocalForward 8765 127.0.0.1:8765
  LocalForward 8766 127.0.0.1:8766     # a second miner's service, if one runs
  ExitOnForwardFailure yes
  ServerAliveInterval 30
  ServerAliveCountMax 2
```

A per-user launchd agent keeps `ssh -N gbox` running and restarts it whenever it exits, so a tunnel killed by
system sleep comes back within about a minute of wake without any action. The key's passphrase is held in the
macOS keychain. Moving the service to another machine changes only `HostName`.

## Part 2: the multi-dashboard guard

**Problem.** Every open dashboard polls the miner directly from its browser every 10 s (`dbg/minerinfo` and
`dbg/icinfo` each cycle; `mcb/setting`, `mcb/status` and `cpb/hshistory` every 60 s), on top of the service's
own poll. Two open pages double the load on firmware known to crash under bursts. Each page also hands its
miner token to the service every minute, so two logged-in browsers overwrite each other's token.

**Rule.** Per service, at most one page, the **lease holder**, polls the miner and hands over its token.

**Endpoint.** `POST /api/viewer`, JSON body:

| Field | Meaning |
|---|---|
| `id` | random per page load, kept in memory only |
| `label` | a short platform word from the browser (`Mac`, `Windows`, `Linux`, ...), for the banner; every tunnelled page arrives from 127.0.0.1, so the address cannot tell them apart |
| `visible` | `document.visibilityState === "visible"` |
| `new` | `true` only on the first check-in after the page loads |
| `take` | `true` when the owner presses Take over |

Reply: `{"holder": <bool>, "holder_label": ..., "holder_since": ...}`.

**Service state.** An in-memory table of `{id, label, first_seen, last_seen, visible}`, capped at 16 entries
(oldest dropped first), never written to disk. An entry silent for 30 s is dropped; if it held the lease, the
lease becomes free.

**Lease rules, in order.**

1. `take: true` from a visible page: that page takes the lease.
2. `new: true` from a visible page: that page takes the lease (the newest page load wins).
3. Lease free and the page visible: that page takes it.
4. `visible: false` from the holder: the lease becomes free.
5. Otherwise nothing changes.

A hidden page never takes the lease. A page returning from sleep sends `new: false`, so it takes the lease
only if the lease is free; the lease does not jump between machines as each one wakes.

**Page behaviour.**

- Checks in before each 10 s miner poll, and at once on `visibilitychange` to visible.
- **Holder:** polls the miner and hands its token to the service as today.
- **Not holder:** makes no miner requests and does not hand over its token. A banner says where the
  dashboard is open and since when, with a Take over button. Everything from the service keeps updating
  (charts from the log, events, hold, plug, health); per-chip and settings panels are marked stale. Settings
  buttons are disabled; hold and plug buttons stay live, because they go to the service, which already sends
  one request at a time to the miner.
- **Hidden:** stops polling the miner and sends one check-in with `visible: false`.
- **Standalone file mode** (no service): no guard, as today. Documented.

**Security.** The endpoint requires `application/json` like every other POST, so no cross-site page can
send it, and the Host check applies. The worst misuse, a local process taking the lease, pauses a browser
page; the service's own polling and the watchdog are unaffected. No new data is exposed: the label is a
platform word.

## Testing

Automated, and run before merge:

- `tests/test_server.py`, against the fake miner, with an injected clock: each lease rule above; expiry
  after 30 s; a hidden holder frees the lease; a hidden page never takes it; the 16-entry cap; 415 for
  non-JSON, 403 for a forged Host, 400 for a malformed body.
- `tests/app_test.js`: a non-holder makes no miner requests and does not hand over its token; settings
  buttons disabled, service buttons live; banner and Take over; visibility transitions; `new: true` only on
  the first check-in.
- The full suite (all Python modules and the Node tests).

On real things:

- **Token test, before the build (2026-09-24, owner present):** two different browsers on the service's
  machine log in to the real miner in turn; watch whether the first one's token stops working. Read-only
  apart from the two logins. The result is recorded; the guard's token rule stands either way.
- **Guard check:** a scratch service (own `GBOX_DATA`, own port) against the fake miner, never the live
  service; two browser windows; pause, take-over and button states as designed.
- **Mac experiments** (handoff on the owner's transfer drive): E1 tab visibility when the Mac is locked or
  asleep; E2 whether it truly sleeps lid-shut; E3 tunnel reconnect after sleep; E4 the miner's stock UI as
  `localhost:8080`.

**Security review:** one reviewer pass at feature-complete, before merge.

## Release

0.11.0 (new HTTP endpoint), built in a worktree, tag `v0.11.0`. Docs: new `docs/remote-access.md` (host
policy, both keys, the Mac config and launchd agent, why the service stays on loopback);
`docs/security-notes.md` (the endpoint and the guard); a README pointer; the evolution log entry.

## Assumptions not yet verified

- A locked or display-asleep Mac reports its tab hidden (E1). If not, the Mac page keeps a throttled lease
  overnight; harmless.
- Nothing in the served page depends on uninterrupted miner polling. To be checked in planning.
- Two `gbox serve` instances run side by side on 8765 and 8766 (port 8766 in the forwards and `permitopen`).
