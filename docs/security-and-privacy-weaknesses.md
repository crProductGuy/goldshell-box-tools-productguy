# Goldshell miner security and privacy weaknesses

What a Goldshell Box-series miner (MCB_V5 "cloud-box" firmware) exposes, and
what is still on the unit when you sell it or give it away. This page is for
owners. The protocol detail and the tests behind each claim are in
[`security-notes.md`](security-notes.md) and
[`firmware-api.md`](firmware-api.md).

Units examined: an SC-BOX (firmware 2.2.5), an SC5 Pro II, an SC Lite, and
an HS-BOX (MCB_V5_4, firmware 2.2.6). Each claim names the unit it was
checked on. Claims marked **unverified** have not been tested.

## Weaknesses

### The web password protects almost nothing

- The login returns a token that never expires and has no nonce. The SC-BOX
  and an SC5 Pro II returned the same token, character for character
  (2026-09-15), and tokens for these units circulate in public helper
  repositories. Anyone on your network who holds one can read the settings,
  change the pools and restart the miner, whatever your password is.
- The login URL carries the password, encrypted with a fixed key that ships
  in the web UI. Anyone who sees the URL, in a proxy log or a browser
  history, has the equivalent of the password.
- The web backend accepts requests from any origin
  (`Access-Control-Allow-Origin: *`), so any web page opened on a computer
  that can reach the miner can talk to it.
- `GET /mcb/status` answers without a token (SC-BOX, 2026-09-19).
- The cgminer API on TCP port 4028 answers without a token. Its `pools`
  command returns the pool URL and the pool user (SC-BOX, SC5 Pro II, SC
  Lite, HS-BOX). Whether port 4028 also accepts write commands is
  **unverified**.
- Bursts of about 15 requests a second crash the web backend (SC-BOX). It
  restarts on its own, but a hostile machine on your network could keep it
  down.

Treat the miner as an unauthenticated device. Keep it on a network segment
you trust, never port-forward it, and never expose it to the internet.

### Secrets stored in plain text

| Where | What | Unit |
|---|---|---|
| `/mcb/wifisetting` | Wi-Fi network names and passwords | SC-BOX. The HS-BOX reports no Wi-Fi hardware (`exist: false`) |
| `/mcb/pools` | pool URL, pool user (often a wallet address), pool password | all |
| `/mcb/setting` `name` | the unit's MAC address | SC-BOX, HS-BOX |
| `/dbg/syslog` | every settings write echoed in full, passwords included | SC-BOX |
| `/dbg/minersyslog` | the pool URL, pool user and pool password, repeated at each pool connect | SC-BOX (user); HS-BOX (user and password, 2026-09-26) |

Both logs survive a power cut (SC-BOX and HS-BOX, checked 2026-09-25 and
2026-09-26). The HS-BOX's cgminer log was 4.1 MB and about 48,000 lines when
read.

### The smart plug is part of the picture

A TP-Link Kasa plug on its original local protocol (port 9999) answers
anyone on the network without authentication. It reports relay state,
energy readings and schedules. Through `cnCloud get_info` it also reports
the email address of the TP-Link account it is registered to. Anyone on
your network can switch it.

A plug schedule can also switch a miner off without warning. On 2026-09-26
an old lighting schedule on the plug turned an HS-BOX off at 06:30. The
miner logged a hard power cut, and gbox, which was not watching that unit,
saw nothing.

## Before you sell or give away a unit

### What you can clear on the unit

1. **Pools.** In the stock web UI, delete every pool, or replace each one with
   a placeholder. Then read `/mcb/pools` back to confirm.
2. **Wi-Fi.** You can switch Wi-Fi off, but you cannot remove the saved
   networks. On the SC-BOX, every write to `/mcb/wifisetting` left the stored
   credentials in place (2026-09-06). See the next section.

### What you cannot clear on the unit

- **Saved Wi-Fi networks.** The API cannot remove them, and a factory reset
  does not remove them either. The SC-BOX examined was bought second-hand and
  factory reset, and it still held the previous owner's networks.
- **Both logs.** No API endpoint that clears either log is documented.
  Whether a factory reset clears them is **unverified**. This toolkit never
  calls factory reset, so it has not been tested.

Assume the new owner can read everything in those two places.

### What to do off the unit

1. **Wi-Fi.** If the unit ever joined your Wi-Fi, even once during setup,
   change your Wi-Fi password on the router. That makes the copy inside the
   miner worthless.
2. **Pool password.** If your pool password is a real password rather than
   the usual `x`, change it at the pool. It is in the logs.
3. **Wallet address.** A wallet address in the logs is public on-chain
   anyway, but it links you to the unit. If that matters to you, mine to a
   new address before you sell.
4. **The Kasa plug.** If the plug goes with the miner, remove it from your
   TP-Link account and factory reset the plug. Otherwise it keeps reporting
   your account email.
5. **Your router.** Remove the unit's DHCP reservation and any firewall rules
   that name it.
6. **Your gbox machine.** `gbox serve --forget` removes a stored password.
   `~/.gbox/log.csv` and `events.log` hold your unit's history. They contain
   no credentials, but they are yours to delete.

### What the next owner should do

The next owner inherits whatever the unit still holds. Before mining, they
should replace the pools with their own, set a new web password, and keep
the unit on a trusted network segment. A new password does not stop anyone
who holds the shared token, which is the first weakness above.

## Open questions

- Does a factory reset clear `/dbg/minersyslog` and `/dbg/syslog`? The next
  owner-authorized reset on a unit that does not matter would settle it.
- On the HS-BOX, the first ~28,000 log lines predate the current owner's
  setup and carry July dates. The logs' timestamps are unreliable before the
  clock syncs. A pattern search found no pool, user or wallet lines in
  those lines, but a search that finds nothing is not proof that nothing is
  there.
- Does changing the web password change the token? The 2026-09-06 notes
  assumed so. The 2026-09-15 finding that two units share one token argues
  against it. Neither has been tested directly.
