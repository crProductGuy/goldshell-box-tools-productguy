# The stock UI's hidden debug page

The Goldshell web UI has a page it never links to: `/#/debug`. It renders
the same `/dbg/` endpoints this toolkit reads, so it is the quickest way to
see per-chip nonce counts, the fan controller's reasoning, and the miner's
own logs without installing anything. Most owners never find it. Everything
`gbox` shows about chips and errors was first confirmed there.

## How to open it

1. Log in to the miner's web UI as usual, for example `http://192.168.1.100/`.
2. Change the address to `http://192.168.1.100/#/debug` and press Enter. The
   page needs the session the login created, so open it in the same browser.

The page is plain and the colors are loud. It is a developer view that
shipped in the firmware, not something Goldshell polished for owners.

## What it shows, and where `gbox` gets the same thing

| On the debug page | Endpoint behind it | Where the toolkit shows it |
|---|---|---|
| Chips: one cell per chip with good nonces (`perf`) and bad nonces (`hwerr`), colored by health | `/dbg/icinfo` | the **Chips** table on the dashboard, `gbox chips`, and the `weak_chips` and `nonces_*` columns of the log |
| Miner info: cgminer-style `[key] => value` lines, including `Device Elapsed`, `Accepted`, `Hardware Errors`, `clock`, `fan0`, `fan1`, `tstemp-0`, `rebootcnt`, `overheat` | `/dbg/minerinfo` | the tiles on the dashboard, `gbox status`, and most columns of the log |
| Fan controller log: one line per fan-speed change with the reason (`t:` board sensor, `target_temp:`) | `/dbg/fanctrllog` | the fan chart, indirectly; the log itself is not copied |
| Miner log: cgminer's log, including `Read Nonce Faild 10 Times, Reinit Device!!!` when a chip forces a board reset | `/dbg/minersyslog` | not shown; `rebootcnt` counts the resets it reports |
| System log: the web backend's log, including `Check Token Error` and `Minerd start !!!` | `/dbg/syslog` | not shown |
| Kernel messages, memory, processes, monitor log, miner history | `/dbg/kmsg`, `/dbg/meminfo`, `/dbg/psinfo`, `/dbg/monitorlog`, `/dbg/minerhistory` | not shown |

The exact set and names of the page's sections have only been checked on an
SC-BOX with the `MCB_V5` cloud-box firmware. If your model shows something
else, the endpoint table in `firmware-api.md` is the authoritative list.

The page also works on the larger units. On an SC5 Pro II (`MCB_V3_3`,
firmware 2.2.0) its owner reported the MINER, CHIPS and MINERSYSLOG tabs
working when logged in, and on 2026-09-15 `/dbg/minerinfo` behind it
answered 200 to the browser and to `curl`, with four `[PGAn]` blocks, one
per hashboard. Another developer's SC Lite notes describe a "debug lock" on
these endpoints. No unit gbox has data from has shown one; a single 401 on
a `/dbg/` request is the token race described in `firmware-api.md`, and a
retry clears it.

## Two cautions

- **The page is heavy on the miner.** Each `/dbg/` file is regenerated on
  every request, and `fanctrllog` grows to about 1 MB. The web backend
  crashes under request bursts and takes the hashrate history buffer with
  it. Do not leave the debug page auto-refreshing, and do not hammer it
  while `gbox serve` is polling; one open tab, refreshed by hand, is fine.
- **The logs carry identifiers.** The miner log names your pool and worker,
  which is usually your wallet address. Read a screenshot before you post
  it.

## Why it matters for a weak chip

The Chips view is the only place in the stock UI where a marginal chip is
visible. The Miner page reports hashrate and shares as if the board were
healthy right up until the resets pull the hashrate down. On the SC-BOX
that started this project, chip 8 showed thousands of bad nonces on the
debug page while the Miner page showed nothing wrong. The dashboard's Chips
table and the Clock trials table exist so you do not have to keep that page
open; `docs/clock-tuning.md` is what to do once you have seen the chip.
