"""A TCP stand-in for a TP-Link Kasa plug on the legacy port-9999 protocol.

Used by the unit tests and by hand:

    python -m tests.fake_plug --port 9998
    gbox power init --host 127.0.0.1:9998

It speaks the autokey-XOR framing with its own copy of the codec (on
purpose: the tests then prove the driver interoperates, not that it agrees
with itself), answers get_sysinfo, set_relay_state and emeter get_realtime
in either field shape, and has knobs to hang, to fail one command, to split
a reply across two writes, and to answer UDP discovery on a chosen port.
"""
import argparse
import json
import socket
import struct
import threading
import time

KEY = 171


def xor_encrypt(text):
    key, out = KEY, bytearray()
    for b in text.encode():
        key ^= b
        out.append(key)
    return bytes(out)


def xor_decrypt(data):
    key, out = KEY, bytearray()
    for b in data:
        out.append(key ^ b)
        key = b
    return out.decode(errors="replace")


class FakePlug:
    def __init__(self, host="127.0.0.1", port=0, model="HS110(US)", alias="fake plug",
                 device_id="8006FAKE0000000000000000000000000000FAKE", meter="old", watts=188.0,
                 relay=1, hw="1.0", fw="1.2.6 Build 200727 Rel.121701", udp_port=None):
        self.host, self.port = host, port
        self.model, self.alias, self.device_id = model, alias, device_id
        self.meter = meter                  # None, "old" (W, V, A, kWh) or "new" (mW, mV, mA, Wh)
        self.watts = watts
        self.relay = relay
        self.hw, self.fw = hw, fw
        self.on_since = time.time()
        self.hang = False                   # accept, never answer
        self.fail_once = None               # method name whose next call closes without a reply
        self.split_reply = False            # write the reply in two pieces with a pause
        self.commands = []                  # (module, method, args) received, in order
        self.udp_port = udp_port            # None: no discovery listener; 0: pick a free port
        self._srv = None
        self._udp = None
        self._threads = []
        self._stop = threading.Event()

    # ------------------------------------------------------------ protocol

    def sysinfo(self):
        return {
            "sw_ver": self.fw, "hw_ver": self.hw, "type": "IOT.SMARTPLUGSWITCH", "model": self.model,
            "mac": "00:00:00:00:00:00", "dev_name": "Smart Wi-Fi Plug", "alias": self.alias,
            "relay_state": self.relay, "on_time": int(time.time() - self.on_since) if self.relay else 0,
            "active_mode": "none", "feature": "TIM:ENE" if self.meter else "TIM", "updating": 0,
            "rssi": -58, "led_off": 0, "deviceId": self.device_id, "err_code": 0,
        }

    def emeter(self):
        if self.meter is None:
            return {"err_code": -1, "err_msg": "module not support"}
        if self.meter == "old":
            return {"current": self.watts / 120.0, "voltage": 120.0, "power": self.watts, "total": 22.0, "err_code": 0}
        return {"current_ma": int(self.watts / 120.0 * 1000), "voltage_mv": 120000,
                "power_mw": int(self.watts * 1000), "total_wh": 22000, "err_code": 0}

    def handle(self, cmd):
        """The reply for one request dict, or None to close without answering."""
        reply = {}
        for module, methods in cmd.items():
            for method, args in methods.items():
                self.commands.append((module, method, args))
                if self.fail_once == method:
                    self.fail_once = None
                    return None
                if module == "system" and method == "get_sysinfo":
                    reply.setdefault("system", {})["get_sysinfo"] = self.sysinfo()
                elif module == "system" and method == "set_relay_state":
                    self.relay = 1 if args.get("state") else 0
                    if self.relay:
                        self.on_since = time.time()
                    reply.setdefault("system", {})["set_relay_state"] = {"err_code": 0}
                elif module == "emeter" and method == "get_realtime":
                    reply.setdefault("emeter", {})["get_realtime"] = self.emeter()
                else:
                    reply.setdefault(module, {})[method] = {"err_code": -1, "err_msg": "module not support"}
        return reply

    # ------------------------------------------------------------ servers

    def _serve_tcp(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_conn, args=(conn,), daemon=True).start()

    def _serve_conn(self, conn):
        with conn:
            try:
                conn.settimeout(5.0)
                hdr = conn.recv(4)
                if len(hdr) < 4:
                    return
                n = struct.unpack(">I", hdr)[0]
                body = b""
                while len(body) < n:
                    chunk = conn.recv(n - len(body))
                    if not chunk:
                        return
                    body += chunk
                if self.hang:
                    self._stop.wait(10.0)
                    return
                reply = self.handle(json.loads(xor_decrypt(body)))
                if reply is None:
                    return
                enc = xor_encrypt(json.dumps(reply))
                frame = struct.pack(">I", len(enc)) + enc
                if self.split_reply:
                    conn.sendall(frame[:5])
                    time.sleep(0.05)
                    conn.sendall(frame[5:])
                else:
                    conn.sendall(frame)
            except OSError:
                return

    def _serve_udp(self):
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(8192)
            except OSError:
                return
            try:
                reply = self.handle(json.loads(xor_decrypt(data)))
            except ValueError:
                continue
            if reply is not None and not self.hang:
                self._udp.sendto(xor_encrypt(json.dumps(reply)), addr)

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        self.port = self._srv.getsockname()[1]
        t = threading.Thread(target=self._serve_tcp, name="fake-plug-tcp", daemon=True)
        t.start()
        self._threads.append(t)
        if self.udp_port is not None:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.bind((self.host, self.udp_port))
            self.udp_port = self._udp.getsockname()[1]
            t = threading.Thread(target=self._serve_udp, name="fake-plug-udp", daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self):
        self._stop.set()
        for s in (self._srv, self._udp):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def address(self):
        return "%s:%d" % (self.host, self.port)


def main():
    p = argparse.ArgumentParser(description="fake Kasa plug on the legacy protocol")
    p.add_argument("--port", type=int, default=9998)
    p.add_argument("--udp-port", type=int, default=None, help="also answer discovery on this UDP port")
    p.add_argument("--meter", choices=["old", "new", "none"], default="old")
    p.add_argument("--watts", type=float, default=188.0)
    a = p.parse_args()
    plug = FakePlug(port=a.port, udp_port=a.udp_port, meter=None if a.meter == "none" else a.meter, watts=a.watts)
    plug.start()
    print("fake plug on %s (udp %s), relay %s, meter %s, %.0f W" % (plug.address, plug.udp_port, plug.relay, a.meter, a.watts))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        plug.stop()


if __name__ == "__main__":
    main()
