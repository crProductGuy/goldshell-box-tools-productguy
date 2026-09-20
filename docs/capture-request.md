# Capturing your miner's API answers for gbox

gbox reads the Goldshell Box firmware's own HTTP API. Every parser in it
was written against one SC-BOX. Other models (SC Lite, HS Box, SC-BOX II,
KD-BOX and relatives) run the same firmware family but answer with
different shapes: several hashboards instead of one, a different power
plan string, a debug section that may be locked. To support a model
without having one on the bench, the project needs a few raw answers from
a real unit. This page says what to capture, how, and what to remove
before sending it.

Nothing here changes a setting. Every request is a read. Fifteen minutes,
no software to install beyond `curl`, which Windows 10 and 11, macOS and
every Linux already have.

## What the capture is used for

- The exact `model` string, so the model table can name your unit.
- The power plan string in your firmware's dialect, so the clock control
  parses it instead of refusing it.
- The per-board data, so the dashboard can show one panel per board.
- Whether the `/dbg/` endpoints answer on your firmware, which decides
  which sampling path gbox takes.

Support for a model is claimed only from a capture like this, or from a
tester's command and its output. See `docs/plan.md`, "Mark's answers,
2026-09-12".

## Before you start: what is sensitive, and what is not

Do not send:

- Your password, or the encrypted hex form of it, or the token. The
  token is password-equivalent on this firmware and never expires
  (`docs/security-notes.md`).
- `/mcb/pools`: the pool URL, your wallet and your worker password. It is
  not in the list in Step 2. Do not add it.
- `/mcb/wifisetting`: your WiFi names and passwords in plain text. Not in
  the list. Do not add it.
- The miner's syslogs (`/dbg/syslog`, `/dbg/minersyslog`): they carry the
  pool wallet. Not in the list.
- The `pools` command on port 4028: the pool URL and user, handed to
  anyone who asks. Step 2 sends `devs` and `summary` only. Do not add it.

Fine to send, after one edit: `/mcb/setting` has a `name` field that is
the unit's MAC address. Replace it with `00:11:22:33:44:55` before
sending. Everything else in the list is hashrate, temperatures,
counters and version strings.

## Step 1: get a token

The firmware wants an `Authorization` header on every request. Two ways
to get the token.

**From the stock web UI (no tools).** Log in to the miner's page in
Chrome, Edge or Firefox. Open the developer tools (F12), pick the Console
tab, and type:

```
localStorage.getItem('token')
```

Copy the long string it prints (three dot-separated parts), without the
quotes. That is the token.

**From the command line (a clone of this repo and Python 3.8 or newer).**
The login is a GET with the password encrypted under a fixed key. This
prints the encrypted form:

```
python -c "from gbox import aes; print(aes.encrypt_password('YOUR-PASSWORD'))"
```

Then log in and read the token out of the answer:

```
curl -s "http://MINER/user/login?username=admin&password=HEX&cipher=true"
```

Replace `MINER` with the miner's address and `HEX` with the printed
string. The answer is `{"JWT Token": "..."}`. Both commands leave the
password in your shell history; clear it afterwards if that matters
where you are.

Put the token in a variable so the commands in Step 2 can be pasted:

```
TOKEN=eyJ...              # bash, zsh (macOS, Linux, Git Bash)
$TOKEN = "eyJ..."         # PowerShell
```

## Step 2: the captures, one request at a time

The firmware's token check has a race, and a burst of requests crashes
its web backend, which then restarts and loses the hashrate history. So:
one request at a time, a pause between them, exactly as written. Do
not put these in a loop without the sleep.

### First, the two reads that need no token (port 4028)

The firmware also answers the classic cgminer socket API on TCP port
4028, with no token and no login. It answered on an SC-BOX and on an
SC5 Pro II, and its `devs` reply carries every board's hashrate,
temperatures, fans and error counts in valid JSON. It does not go through
the web backend, so it has neither the token race nor the burst crash.
This is the read `gbox serve` tries first, which makes these two files
the most useful ones in the folder. If the port is closed on your unit,
the command hangs or says "connection refused"; say so in your note, that
is a finding too.

bash, zsh, Git Bash (no extra tool; `/dev/tcp` is part of bash):

```
M=MINER-ADDRESS
mkdir capture && cd capture
exec 3<>/dev/tcp/$M/4028; printf '{"command":"devs"}'    >&3; cat <&3 > api4028_devs.json;    exec 3<&-; sleep 2
exec 3<>/dev/tcp/$M/4028; printf '{"command":"summary"}' >&3; cat <&3 > api4028_summary.json; exec 3<&-; sleep 2
```

The same with netcat, if you have it. The `-q 1` makes `nc` wait for the
answer after it has sent the request; without it some builds close first
and write an empty file (the BSD `nc` on macOS has no `-q` and does not
need it):

```
echo '{"command":"devs"}'    | nc -q 1 $M 4028 > api4028_devs.json;    sleep 2
echo '{"command":"summary"}' | nc -q 1 $M 4028 > api4028_summary.json; sleep 2
```

PowerShell 7:

```
$M = "MINER-ADDRESS"
mkdir capture; cd capture
foreach ($cmd in "devs", "summary") {
  $c = [Net.Sockets.TcpClient]::new($M, 4028); $s = $c.GetStream()
  $b = [Text.Encoding]::ASCII.GetBytes("{`"command`":`"$cmd`"}"); $s.Write($b, 0, $b.Length)
  [IO.StreamReader]::new($s).ReadToEnd().TrimEnd([char]0) > "api4028_$cmd.json"; $c.Close(); sleep 2
}
```

Each reply ends in one NUL byte. Leave it or strip it, gbox takes both.

### Then the seven HTTP reads, with the token

bash, zsh, Git Bash (the same shell and folder as above):

```
curl -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/status"                   > mcb_status.json;      sleep 2
curl -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/setting"                  > mcb_setting.json;     sleep 2
curl -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/cgminer?cgminercmd=devs"  > cgminer_devs.json;    sleep 2
curl -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/algosetting"              > mcb_algosetting.json; sleep 2
curl -s -w "\nHTTP %{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://$M/dbg/minerinfo" > dbg_minerinfo.txt; sleep 2
curl -s -w "\nHTTP %{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://$M/dbg/icinfo"    > dbg_icinfo.json;   sleep 2
curl -s -H "Authorization: Bearer $TOKEN" "http://$M/cpb/hshistory"                > cpb_hshistory.json
```

PowerShell 7, in the same window and folder as above (use `curl.exe`, not
the `curl` alias, which is a different command):

```
curl.exe -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/status"                  > mcb_status.json;      sleep 2
curl.exe -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/setting"                 > mcb_setting.json;     sleep 2
curl.exe -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/cgminer?cgminercmd=devs" > cgminer_devs.json;    sleep 2
curl.exe -s -H "Authorization: Bearer $TOKEN" "http://$M/mcb/algosetting"             > mcb_algosetting.json; sleep 2
curl.exe -s -w "`nHTTP %{http_code}`n" -H "Authorization: Bearer $TOKEN" "http://$M/dbg/minerinfo" > dbg_minerinfo.txt; sleep 2
curl.exe -s -w "`nHTTP %{http_code}`n" -H "Authorization: Bearer $TOKEN" "http://$M/dbg/icinfo"    > dbg_icinfo.json;   sleep 2
curl.exe -s -H "Authorization: Bearer $TOKEN" "http://$M/cpb/hshistory"               > cpb_hshistory.json
```

If the very first file comes back empty or says `401`, your firmware may
want the header without the word `Bearer`: the SC-BOX takes
`Authorization: Bearer <token>`, and one SC Lite helper on GitHub sends
`Authorization: <token>`. Try `mcb/status` again with the other form,
and say which one worked in your note.

What each one is for:

| File | Tells gbox |
|---|---|
| `api4028_devs.json` | every board's hashrate, temperatures, fans, error counts and reset count, and on the larger units the firmware's own voltage and current. The service's first-choice read |
| `api4028_summary.json` | the unit totals, to check the per-board sums against |
| `mcb_status.json` | the exact `model`, `firmware`, `hardware` and `mcbversion` strings |
| `mcb_setting.json` | the power plan dialect (`manualPowerplan` and every `powerplans[].info`), whether `temp_targets` exists and its range, the fan target |
| `cgminer_devs.json` | per-board hashrate, temperature, fan speed and error counts on units with several boards. On the SC-BOX this endpoint answers HTTP 500; on the SC Lite it is the per-board source. An error answer is a useful capture too |
| `mcb_algosetting.json` | the algorithm list, for the model table |
| `dbg_minerinfo.txt` | the same per-board data as text, one `[PGAn]` block per board. The dashboard page reads this one, because a browser cannot open port 4028. If it answers `401` or "Debug access is locked", that is the finding: keep the file, it says so |
| `dbg_icinfo.json` | per-chip counts per board; the `tabledata` names the boards |
| `cpb_hshistory.json` | the miner's own hashrate buffer; optional, it is large and only confirms the sample rate |

A single `HTTP 401` on a `/dbg/` line is usually the token race, not a
lock: wait ten seconds and run that one line again. On an SC5 Pro II
(`MCB_V3_3`, firmware 2.2.0) the first capture's two 401s never came
back in three retests. If it says `HTTP 401` every time, open the stock UI's hidden debug
page (`http://MINER/#/debug`; `docs/stock-ui-debug-page.md` describes
it), unlock it if it asks, and run the two `/dbg/` lines again into
`dbg_minerinfo.after-unlock.txt` and `dbg_icinfo.after-unlock.json`.
Both files, before and after, are wanted: they tell whether the unlock
is per session or permanent.

## Step 3: redact

- Open `mcb_setting.json` and replace the value of `name` with
  `00:11:22:33:44:55`.
- Search every file for your pool worker name and wallet, in case a
  firmware puts them somewhere unexpected. `grep -l stratum *` finds
  a pool URL; a wallet is a long base-58 or hex string.
- Make sure the token is not in any file. It is only in the request
  header, so it should not be, but check.

## Step 4: what to send, and where

The `capture` folder, zipped, plus three lines of text:

1. Model and firmware as the stock UI's About or Status page shows them.
2. How many hashboards the unit has, and how many fans.
3. Whether the stock UI's Miner page offers a fan-target slider, and if
   so its range.

Send it to whoever pointed you at this page, or attach it to an issue at
github.com/crProductGuy/goldshell-box-tools-productguy. The capture goes
into `tests/fixtures/<model>/` with a README line crediting you by
whatever name you choose, or none.

## What happens with it

The capture becomes the fixture the tests run against, so the model's
parsing is exercised on every change. Before the release claims support,
someone with the unit runs `python -m gbox status` and `python -m gbox
chips` from a clone and sends the output. That is the second half of the
ask, and it comes later.

## One-line summary for a message

> Could you run two socket reads and seven `curl` reads against your
> miner (read-only, one at a time, fifteen minutes), replace one MAC
> address, and send me the folder? Instructions: `docs/capture-request.md` in the gbox repo. The
> pool and WiFi endpoints are deliberately not in the list.
