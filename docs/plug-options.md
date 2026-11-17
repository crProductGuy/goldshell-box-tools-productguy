# Smart plug options for gbox

Which locally controlled plugs with energy monitoring suit gbox. Written
2026-11-08 from this repo's code and plan and one round of web searches;
decisions added 2026-11-17. Status of every claim is marked:
**verified** (read from this repo or run), **sourced** (a page read in
search results, not opened), or **unverified**.

## What gbox can drive today

**Verified** (`gbox/plug.py`): one driver, `KasaLegacy`. It speaks TP-Link's
original protocol on TCP 9999, with no authentication, and reads the energy
meter when the plug has one (`get_sysinfo`, `set_relay_state`, `emeter`).
`discover()` also recognises plugs that speak the newer KLAP protocol, but
only to report them. It cannot control them.

The planned order of further drivers is in `docs/plan.md`: Shelly Gen2, then
Tasmota, then a generic HTTP driver for REST PDUs, then KLAP. **None is
built.** A Zigbee, Z-Wave or Matter plug cannot be driven by gbox at all.

## Ranked options for gbox

| Rank | Plug | Works in gbox today | Local control | Energy monitor | Main risk |
|---|---|---|---|---|---|
| 1 | Kasa HS110 or KP115 on old firmware | Yes | TCP 9999, no login | Yes | A firmware update moves it to KLAP, which gbox cannot drive. Stock sold new may already be on newer firmware (unverified) |
| 2 | Shelly Plug US (Gen3 or Gen4) | No, needs a driver | HTTP on the LAN, no cloud | Yes (sourced) | UL status unclear: one listing calls the Plus Plug US ETL-certified, UL claims found refer to Gen4 only (sourced). Read the label |
| 3 | Tasmota or ESPHome plug (for example Athom US) | No, needs a driver | HTTP or MQTT, no cloud | Yes | Small brand, UL listing not verified, bulky |
| 4 | Kasa or Tapo on KLAP (KP125M, P110, P115) | No | Local after login, needs the TP-Link account credentials | Yes | Credentials to store; reported local-auth failure on newest P115 firmware (python-kasa issue 1752, sourced); cannot be tested here without owning one |
| 5 | Zigbee, Z-Wave, Matter plugs | No | Via a coordinator or controller | Varies | No gbox driver and none planned |

Recommendation: keep the HS110 on the SC-BOX as it is. If a second metered
plug is needed before a Shelly driver exists, buy a second legacy-firmware
Kasa and decline every firmware update (`docs/kasa-plugs.md`). Build the
Shelly driver when a plug other than Kasa is wanted.

**Availability, sourced only:** a 2020 TP-Link community thread lists the
HS110 as End of Life on TP-Link's US site, with the KP115 named as its
replacement. No 2026 listing was found, so whether a new HS110 can still be
bought, and on what firmware, is **unverified**.

## One finding to test: Kasa after an outage with no internet

**Sourced, not tested on this hardware.** A TP-Link community thread quotes
TP-Link support saying a Tapo P110 syncs its clock from the internet, and
after a power outage with no internet "all time-related features will
stop". A user reported a "zombie mode": on and off still work, but energy
monitoring and settings are lost until the plug sees the internet once. That
was a Tapo, not an HS110 on the legacy protocol. Whether an HS110 does the
same is **unverified**. It matters because the SC-BOX plug feeds the
watchdog, and the watchdog uses the meter to tell "powered but hung" from
"never powered up". The ISP drops within minutes of a grid loss, so the
condition is the normal outage case.

Test: block the router's WAN, power-cycle a bench plug, then read
`get_sysinfo` and `emeter`. Settled when watts read after boot.

## Decisions and open items

Decided by the owner on 2026-11-17 (register IDs are the project code plus a
number):

| ID | Decision |
|---|---|
| GB-085 | Yes. Build the Shelly driver after gate 2, and buy one Shelly Plug US first to verify its local API on real hardware. |
| GB-086 | Yes. Run the no-internet meter test above. Not yet scheduled. |
| GB-087 | Yes, if available. Nothing bought. A new HS110 may not be sold (see Availability). |
| GB-089 | Yes. Check the Shelly page and label for UL listing before buying. Gen4 only if UL is shown. |

Open: GB-258 (when and on which plug to run the test), GB-260 (the spare
when no new HS110 is sold).

## Sources

- [HS110 End of Life thread (TP-Link community)](https://community.tp-link.com/en/smart-home/forum/topic/220546)
- [Shelly Plug US review (Home Tech Hacker)](https://hometechhacker.com/shelly-plug-us-is-it-the-best-smart-plug/)
- [Shelly Plus Plug US (Gearbrain)](https://www.gearbrain.com/devices/4588DA9E5A3711ED8E8341B1B269F696.html)
- [python-kasa issue 1752](https://github.com/python-kasa/python-kasa/issues/1752)
- [TP-Link community thread on Tapo P110 clock sync after an outage](https://community.tp-link.com/en/smart-home/forum/topic/719736)
