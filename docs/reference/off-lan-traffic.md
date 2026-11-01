---
title: What a Goldshell BOX sends off the LAN (find.goldshell.com)
---

# What a Goldshell BOX sends off the LAN (find.goldshell.com)

The SC-BOX's web backend posts to `find.goldshell.com/api/miners`, apparently
for Goldshell's find-my-miner feature. Evidence:
[notebook, What the firmware sends off the LAN](../firmware-api.md#what-the-firmware-sends-off-the-lan).

## What it sends

`[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09]`, from the miner's own log (which one and the day
are not recorded in the notebook).

- Each post carries the model, serial number, MAC and LAN address. The server
  sees the home's public address from the connection.
- The log showed 41 posts over about a day. 22 were answered
  `{"status":"ok"}`. The notebook does not say what the other 19 got.
- No pool, wallet or hashrate was in them.

## Is it needed?

- The notebook states it is not needed for mining. `[unverified]`: no test of
  a blocked unit over a full day is on record.
- An owner who wants it stopped can block that one name at the resolver.
- **Block only that name.** The miner sets its clock over NTP, and a cold start
  reads 2007 until it does. The miner has no real-time clock.
  `[verified: SC-BOX, MCB_V5_4, fw 2.2.5, 2026-09-21]`
  ([clock behavior](behavior-and-failure-modes.md#the-miners-clock))

## Other models

Whether the HS-BOX, SC5 Pro II or SC Lite do the same is `[unverified]`.

## How to check your own unit

The notebook counted the posts from the miner's log without naming which one.
The web backend's `/dbg/syslog` is the likely place (`[unverified]`). Look for
lines naming `find.goldshell.com` on the stock UI's `/#/debug` page. Remove your
MAC and serial before you share a capture.
