# Kasa smart plugs: how they take commands

How a TP-Link Kasa plug on the original local protocol decides to switch, and
what that means for a plug that powers a miner. It ends with a worked example:
a plug that switched a miner off at 06:30 on three mornings in a row, with no
schedule on it.

Units examined: an HS105 (firmware 1.5.6, no energy meter) and an HS110 (with
meter), both on the original protocol. For the protocol details, see
`gbox/plug.py`. For how gbox uses a plug, see
[Power-cycling a hung miner](power-cycle.md).

## Three ways a command reaches the plug

**From the LAN.** The plug listens on TCP port 9999 and answers JSON commands
obfuscated with a fixed XOR key. There is no password. Anything on your
network can read the plug's state and switch it. gbox uses this path, and its
power cycles on the HS110 prove that an unauthenticated off command works.

**From the cloud.** The plug keeps its own outbound connection to TP-Link's
servers. The Kasa app sends commands through that connection, which is why the
app works on mobile data, away from home. Automations that live in someone
else's cloud also arrive this way: Kasa Smart Actions and Scenes, Alexa and
Google Home routines, IFTTT, SmartThings. Nothing on your network sees these
commands arrive except the plug.

**From the plug itself.** Schedules, countdown timers, and away mode are stored
on the plug and run on its own clock, set over the internet. They fire whether
or not the app is open.

## Reading what the plug holds

These commands only read. None of them changes anything:

| Command | What it tells you |
|---|---|
| `system.get_sysinfo` | `relay_state` (1 on, 0 off) and `on_time`, the seconds since the relay last switched on |
| `schedule.get_rules` | the schedule rules stored on the plug |
| `count_down.get_rules` | a running countdown timer |
| `anti_theft.get_rules` | away mode, which switches the plug at random |
| `schedule.get_next_action` | the next switch the plug plans; `type -1` means none |
| `cnCloud.get_info` | whether the plug is bound to an account and connected to the cloud |

If all the rule lists are empty, the next action is `-1`, and the relay still
switches, the command came from outside the plug. The plug cannot tell you
where from: nothing in its replies records who sent a command.

Don't rely on `on_time` as a history of the relay. See the note at the end of
the worked example.

## If a plug powers your miner

- Give the plug one job. No schedules, no scenes, no routines, and no away mode
  on a plug that feeds a miner.
- Before you move a plug from another job, factory-reset it and add it to the
  app again. A reset clears what the plug holds. Whether it also clears a rule
  held in the cloud, as in the example, is unverified.
- Decline firmware updates offered in the Kasa app. Units updated from 2023 to
  2025 moved to a newer protocol that gbox cannot drive.
- If you don't need gbox's watchdog on a miner, don't put a plug in front of
  it. A plug can only add a way to lose power.

## Worked example: off at 06:30, three mornings running

The owner's second miner, an HS-BOX, ran behind an HS105. gbox had no
watchdog on it; the plug was there for switching it on and off by hand. The
plug had earlier switched a UV lamp that ran overnight in a basement against
mildew, and the owner had set a rule, months before, to switch the lamp off at
06:30.

| Morning | Off | Back on | How the time is known |
|---|---|---|---|
| 2026-09-26 | 06:30:00 | about 10:00, by hand | the miner's own log |
| 2026-09-27 | between 06:29:57 and 06:30:02 | 10:53, by hand | the miner's own log |
| 2026-09-28 | between 06:29:46 and 06:30:02 | 09:32, plugged straight into the wall | the plug and the miner, read every 15 seconds |

About 11 hours of mining were lost across the three mornings. On 09-27 the
miner's log ran normally up to 06:29:57, with no error or shutdown line: its
power stopped.

After the first morning, the owner disabled the old rule in the Kasa app and
then deleted it, around midday on 09-26. On 09-27 the plug held no schedule,
countdown, or away-mode rule, and the owner found none in the app. Alexa did not
know the plug. Google Home, HomeKit, IFTTT, SmartThings, Kasa Smart Actions, a
second phone, and gbox itself were each ruled out.

On 09-28 a read-only script read the plug every 15 seconds from 06:15 to 06:40.
Up to 06:29:46 every rule list was empty and the next action was `-1`. At
06:30:02 the relay was off, and the plug went on answering every read. The
plug did not fail. Its relay was switched off.

So the command came from outside the plug, at the minute of a rule the owner
had deleted. The likeliest explanation is that a copy of the deleted rule
survives in TP-Link's cloud and still fires. That is not proven, because the
plug cannot say who sent a command. A device on the LAN is the other
possibility, and no candidate for it was found.

The owner took the plug out on 09-28:

> "This HS BOX 'just works', so I don't think we need the power cycle
> capability on it."

The miner has been powered straight from the wall since.

**A note on `on_time`.** On 09-28 the plug reported its relay on since 00:01.
The miner's uptime showed continuous power since about 21:46 the evening
before, so the relay had not switched at 00:01. Something reset the counter
without switching the relay, possibly a restart of the plug's own software.
It is why this page does not treat `on_time` as a record of when power was
switched.
