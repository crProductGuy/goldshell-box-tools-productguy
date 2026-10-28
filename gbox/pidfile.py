"""A pid file for the running service, so it can be identified precisely.

Written 2026-09-19, after an agent session cleaning up a scratch service stopped the owner's live
one as well. The two run byte-identical command lines -- `pythonw -m gbox serve` -- so a filter on
the command line cannot tell them apart, and there was nothing else to match on. That was not a
careless shortcut; it was the only handle available, and it cannot work. This gives a real one.

Each service writes `gbox.pid` into its OWN data directory, so the live service's file is at
`~/.gbox/gbox.pid` and a scratch instance's is wherever `GBOX_DATA` points. Whoever wants to stop
one reads that file and uses the pid, rather than matching text.

Standard library only, and every call is best-effort: a service must never fail to start, or fail
to stop, because of this file.
"""
import json
import os
import sys
import time

NAME = "gbox.pid"


def path(data_dir):
    return os.path.join(str(data_dir), NAME)


def _alive(pid):
    """Is a process with this pid running? False on any doubt."""
    try:
        return _alive_unguarded(pid)
    except Exception:                    # a pid ctypes cannot pass (2**40 in a corrupt file raised ArgumentError)
        return False


def _alive_unguarded(pid):
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes                                    # stdlib; no dependency added
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)                                  # POSIX: signal 0 only tests for existence
    except (OSError, ProcessLookupError):
        return False
    return True


def write(data_dir, port, version, bind=None):
    """Record this process. Best-effort: returns the path written, or None. `bind` (0.13.0) is the address the
    service listens on, so `serving` asks the right one."""
    try:
        record = {"pid": os.getpid(), "port": int(port), "version": str(version),
                  "started": time.strftime("%Y-%m-%d %H:%M:%S")}
        if bind is not None:
            record["bind"] = str(bind)
        with open(path(data_dir), "w") as f:
            json.dump(record, f)
        return path(data_dir)
    except OSError:
        return None


def read(data_dir):
    """What the file says, or None if it is missing or unreadable."""
    try:
        with open(path(data_dir)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def running(data_dir):
    """The record for a service that is actually running, or None. A file left behind by a process
    that has since died reads as not running, which is what a caller wants to know."""
    d = read(data_dir)
    return d if d is not None and _alive(d["pid"]) else None


def serving(data_dir, timeout=2):
    """The record for a service that is running AND answers `/api/health` on its recorded port, or None.

    A live pid alone is not enough to refuse a start (release review 4): an unclean exit leaves the file
    behind, Windows hands the pid to another process, and `serve` under pythonw would then exit with nothing
    shown and no watchdog running. The address asked is the one the service recorded it listens on (loopback
    for a wildcard, or for a file from before 0.13.0); a service bound only to a LAN address would otherwise be
    missed and a second one let in (release re-review)."""
    import urllib.request
    d = running(data_dir)
    if d is None:
        return None
    bind = d.get("bind")
    host = "127.0.0.1" if bind in (None, "", "0.0.0.0", "localhost") else ("::1" if bind == "::" else str(bind))
    if ":" in host:
        host = "[%s]" % host
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (host, int(d.get("port"))), timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
        return d if isinstance(body, dict) and body.get("ok") else None
    except Exception:
        return None


def remove(data_dir, only_mine=True):
    """Delete the file on a clean shutdown. By default only if it is this process's own, so a
    service that lost a race does not delete the winner's record."""
    d = read(data_dir)
    if d is None or (only_mine and d.get("pid") != os.getpid()):
        return False
    try:
        os.remove(path(data_dir))
        return True
    except OSError:
        return False
