"""Planned power: off, on and cycle through the configured plug, each under a hold.

The watchdog's power rung cuts power on its own judgment. This module is the
owner's hand on the same relay: the page's Power buttons, `gbox power off`
and `gbox power on`, and the schedule all come through here, so every
planned outage is logged as the owner's act and the watchdog stands down
until the miner is back (`Watchdog.hold_start`). Nothing here counts
against the recovery caps.

Every action first asks the plug who it is and refuses any device but the
one `gbox power init` recorded; a DHCP change must never point this at a
different appliance. `cycle` runs off, wait, on in its own thread so an HTTP
caller gets an answer at once; `state()` says `busy` meanwhile.

Design of record: docs/power-hold-proposal.md.
"""
import datetime
import threading
import time

from . import config
from .plug import PlugError


class PowerRefused(Exception):
    """The action was not taken: wrong plug, plug not answering, or a cycle already running."""


def _who(source, action):
    """`you (page)`, `you (gbox power off)`, or `the schedule`."""
    if source == "schedule":
        return "the schedule"
    return "you (%s)" % ("page" if source == "page" else "gbox power " + action)


def _before(watts):
    return ("%.0f W before" % watts) if watts is not None else "no meter"


class PowerControl:
    def __init__(self, plug, power_cfg, watchdog, events, sleep=time.sleep, clock=time.time):
        self.plug = plug
        self.cfg = dict(power_cfg or {})
        self.watchdog = watchdog            # None runs without holds (gbox serve --no-watchdog)
        self.events = events
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._thread = None
        self.busy = None                    # "cycling" while the cycle thread runs
        self.off_by_you = False             # the relay was opened here and not closed here since

    # ------------------------------------------------------------ reads

    def _check(self):
        """The plug's identity, or PowerRefused. Nothing moves unless the device id matches."""
        try:
            info = self.plug.identify()
        except PlugError as e:
            raise PowerRefused("the plug did not answer (%s); nothing sent" % e) from None
        if info.get("device_id") != self.cfg.get("device_id"):
            raise PowerRefused("the plug is not the device recorded by `gbox power init` (id differs); nothing sent")
        return info

    def _watts(self, info):
        if not info.get("meter"):
            return None
        try:
            return self.plug.watts()
        except PlugError:
            return None

    def _hold(self, minutes, reason, source):
        if self.watchdog is not None:
            self.watchdog.hold_start(minutes, reason, source)

    def state(self):
        """Relay, watts, whether a cycle is running, and the hold, for /api/health and the CLI. Never raises."""
        relay = watts = None
        try:
            info = self.plug.identify()
            relay = bool(self.plug.state())
            watts = self._watts(info)
        except PlugError:
            pass
        return {"relay": relay, "watts": watts, "busy": self.busy, "off_by_you": self.off_by_you,
                "hold": self.watchdog.hold_info() if self.watchdog is not None else None}

    # ------------------------------------------------------------ actions

    def off(self, source):
        """Open the relay under a hold with no expiry: the miner stays off until `on`."""
        with self._lock:
            self._not_busy()
            info = self._check()
            w = self._watts(info)
            try:
                self.plug.off()
            except PlugError as e:
                self.events.write("power: switch off failed: %s" % e)
                raise PowerRefused("switch off failed: %s" % e) from None
            self.off_by_you = True
            self._hold(None, "switched off", source)
            self.events.write("power: switched off by %s" % _detail(_who(source, "off"), _before(w)))
            return self.state()

    def on(self, source):
        """Close the relay under a hold of the settle gap, so the boot is not judged."""
        with self._lock:
            self._not_busy()
            self._check()
            try:
                self.plug.on()
            except PlugError as e:
                self.events.write("power: switch on failed: %s" % e)
                raise PowerRefused("switch on failed: %s" % e) from None
            self.off_by_you = False
            self._hold(self.cfg.get("settle_minutes"), "switched on, booting", source)
            self.events.write("power: switched on by %s" % _who(source, "on"))
            return self.state()

    def cycle(self, source, off_seconds=None):
        """Off, `off_seconds`, on, in a thread; answers at once. The hold covers the boot."""
        with self._lock:
            self._not_busy()
            info = self._check()
            w = self._watts(info)
            seconds = int(off_seconds if off_seconds is not None else self.cfg.get("off_seconds", 15))
            self.busy = "cycling"
            self._hold(self.cfg.get("settle_minutes"), "power cycle", source)
            answer = self.state()               # read before the relay moves: the caller sees "cycling" and the hold
            self._thread = threading.Thread(target=self._run_cycle, args=(source, seconds, w),
                                            name="gbox-power-cycle", daemon=True)
            self._thread.start()
            return answer

    def _run_cycle(self, source, seconds, w):
        try:
            self.plug.cycle(seconds, sleep=self._sleep)
            self.off_by_you = False
            self.events.write("power: cycled by %s: off %d s, on (%s)" % (_who(source, "cycle"), seconds, _before(w)))
        except Exception as e:
            self.events.write("power: cycle failed: %s (%s)" % (e, _before(w)))
        finally:
            self.busy = None

    def join(self, timeout=None):
        """Wait for a running cycle (tests and the CLI)."""
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _not_busy(self):
        if self.busy:
            raise PowerRefused("a power cycle is running; wait for it to finish")


def _detail(who, before):
    """`you (page; 197 W before)` or `the schedule (197 W before)`."""
    if who.endswith(")"):
        return who[:-1] + "; " + before + ")"
    return who + " (" + before + ")"


class Scheduler:
    """Off and on at set local times on the allowed days, edges only.

    The poller calls `tick()` after each sample. A set time that falls
    between the previous tick and this one fires the matching control
    call. Nothing fires on the first tick, so a service that starts inside
    an off window leaves the miner as it found it, and an owner's On inside
    the window holds until the next off time.
    """

    def __init__(self, schedule, control, events, clock=time.time):
        self.off = config.parse_hhmm(schedule["off"])
        self.on = config.parse_hhmm(schedule["on"])
        self.days = list(schedule.get("days") or config.DAYS)
        self.control = control
        self.events = events
        self._clock = clock
        self._last = None

    def describe(self):
        days = "every day" if set(self.days) == set(config.DAYS) else ", ".join(self.days)
        return "off %02d:%02d, on %02d:%02d, %s" % (self.off + self.on + (days,))

    def tick(self):
        """Fire what fell due since the last tick. Returns "off", "on" or None."""
        now = self._clock()
        last, self._last = self._last, now
        if last is None:
            return None
        fired = None
        for name, hm in (("off", self.off), ("on", self.on)):
            if self._crossed(hm, last, now):
                fired = name
                try:
                    getattr(self.control, name)("schedule")
                except Exception as e:
                    self.events.write("power: schedule could not switch %s: %s" % (name, e))
        return fired

    def _crossed(self, hm, last, now):
        base = datetime.datetime.fromtimestamp(last).replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        for k in range(3):                  # the day of the last tick and the two after it
            cand = base + datetime.timedelta(days=k)
            if last < cand.timestamp() <= now and config.DAYS[cand.weekday()] in self.days:
                return True
        return False
