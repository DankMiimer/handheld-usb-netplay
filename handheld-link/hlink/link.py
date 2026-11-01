"""Keeps the link to the other handheld up, on either end of the cable.

The handheld that is the USB host talks ADB to the other one and opens two
connections to its file service on 127.0.0.1:55441 (adbd's tcp: service):
on the first it sends requests, and the second it hands over ("reverse")
for the other handheld's requests to it. So both sides end up with a client
to the other's files and a server for their own, whichever side hosts. Each
connection is a group of STREAMS adbd streams carrying one byte stream
(wire.Striped), since one stream of an old adbd manages about 1 MB/s.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time

from . import adb
from .service import Client, PeerError, RemoteSide, Server
from .wire import Conn, Striped

PORT = int(os.environ.get("HLINK_PORT", 55441))
STREAMS = int(os.environ.get("HLINK_STREAMS", 4))
TIMEOUT = 30        # a request with no answer this long: the link is gone
PING_IDLE = 3       # ping when nothing went over the link this long
LOOK_FOR = 30       # seconds the port stays a host with nothing plugged in

WAITING, LINKED, PROBLEM = "waiting", "linked", "problem"


class State:
    """What the screen shows about the link; `version` changes with it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.status = WAITING
        self.text = "Starting"
        self.peer: RemoteSide | None = None
        self.peer_info: dict = {}
        self.activity = ""
        self.activity_at = 0.0
        self.version = 0

    def set(self, status: str, text: str, peer=None, info=None) -> None:
        with self.lock:
            self.status, self.text = status, text
            self.peer, self.peer_info = peer, (info or {})
            self.version += 1

    def note(self, text: str) -> None:
        with self.lock:
            self.activity, self.activity_at = text, time.monotonic()
            self.version += 1


class TimedSock:
    """A stream whose silent waits end as ConnectionError, not a hang."""

    def __init__(self, s) -> None:
        self.s = s
        s.settimeout(TIMEOUT)

    def recv(self, n):
        try:
            return self.s.recv(n)
        except (socket.timeout, TimeoutError):
            raise ConnectionError("the other handheld stopped answering") from None

    def sendall(self, b):
        try:
            self.s.sendall(b)
        except (socket.timeout, TimeoutError):
            raise ConnectionError("the other handheld stopped answering") from None

    def close(self):
        self.s.close()


class Manager(threading.Thread):
    def __init__(self, server: Server, info: dict, log) -> None:
        super().__init__(daemon=True, name=self.__class__.__name__)
        self.server = server
        self.info = info
        self.log = log
        self.state = State()
        self.stopping = threading.Event()

    def stop(self) -> None:
        self.stopping.set()

    def look_again(self) -> None:
        """The user asked to try again (A on the waiting screen)."""

    def _serve(self, conn: Conn) -> None:
        threading.Thread(target=self.server.serve, args=(conn,), daemon=True,
                         name="serve").start()

    def _keepalive(self, client: Client, alive) -> str:
        """Ping until the link fails; returns why it did."""
        while not self.stopping.is_set() and alive():
            try:
                client.ping(PING_IDLE)
            except (ConnectionError, PeerError, OSError) as e:
                return str(e) or "the link went down"
            self.stopping.wait(1)
        return "stopped" if self.stopping.is_set() else "the link went down"


class DeviceManager(Manager):
    """The USB device end: wait for the host to connect through adbd."""

    def __init__(self, server, info, log, rebind=None) -> None:
        super().__init__(server, info, log)
        self.rebind = rebind
        self.lock = threading.Lock()
        self.groups: dict = {}

    def run(self) -> None:
        self.state.set(WAITING, self._waiting_text())
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", PORT))
        except OSError as e:
            self.state.set(PROBLEM, "Can't listen for the link: %s" % (e.strerror or e))
            return
        srv.listen(4)
        srv.settimeout(1)
        udc = None
        lost = fixed = 0.0
        while not self.stopping.is_set():
            now = udc_state()
            if now != udc:
                # "configured": a USB host (a PC, the other handheld) took us
                self.log("usb: this handheld's USB device side: %s" % now)
                udc = now
                lost = 0.0 if now == "configured" else time.monotonic()
            t = time.monotonic()
            if self.rebind and lost and t - lost > 5 and t - fixed > 10:
                # no host for a while: on handhelds that can't see the cable
                # (the GBA Mini) only a rebind lets the next host in
                self.log("usb: %s" % self.rebind())
                fixed = t
            try:
                sock, _ = srv.accept()
            except socket.timeout:
                if self.state.status != LINKED:
                    text = self._waiting_text()
                    if text != self.state.text:
                        self.state.set(WAITING, text)
                continue
            threading.Thread(target=self._accepted, args=(sock,), daemon=True).start()
        srv.close()

    def _waiting_text(self) -> str:
        if not adb_running():
            return "Set the USB mode to ADB first (USB Mode in Applications)"
        return "Waiting for the other handheld. Connect the cable to its OTG adapter and open Handheld Link there"

    def _accepted(self, sock) -> None:
        ts = TimedSock(sock)
        conn = Conn(ts)
        try:
            first = conn.recv()
        except ConnectionError:
            conn.close()
            return
        if first.get("op") != "join":
            self.server.serve(conn, first)      # a plain connection (tests)
            return
        try:
            gid, index, of = str(first["group"]), int(first["index"]), int(first["of"])
            role = first.get("role")
            if not 1 <= of <= 16 or not 0 <= index < of or role not in ("serve", "reverse"):
                raise ValueError
        except (KeyError, ValueError, TypeError):
            conn.close()
            return
        # the host opens a group of streams; the last one to arrive takes them all
        with self.lock:
            now = time.monotonic()
            for g in [g for g, v in self.groups.items() if now - v["t"] > 15]:
                for s in self.groups.pop(g)["socks"]:
                    if s:
                        s.close()
            group = self.groups.setdefault(gid, {"socks": [None] * of, "t": now})
            if len(group["socks"]) != of:
                conn.close()
                return
            group["socks"][index] = ts
            if not all(group["socks"]):
                return
            del self.groups[gid]
        socks = group["socks"]
        try:
            Conn(socks[0]).send({"ok": True})
        except ConnectionError:
            for s in socks:
                s.close()
            return
        conn = Conn(Striped(socks))
        if role == "serve":
            self.server.serve(conn)
            return
        # this group carries our requests to the host
        try:
            client = Client(conn)
            info = client.call("hello", **self.info)
        except (ConnectionError, PeerError) as e:
            self.log("link: hello failed: %s" % e)
            conn.close()
            return
        name = info.get("name") or "the other handheld"
        self.log("link: linked with %s (%s)" % (name, info.get("version")))
        self.state.set(LINKED, "Linked with %s" % name, RemoteSide(client, name), info)
        why = self._keepalive(client, lambda: True)
        self.log("link: down: %s" % why)
        client.close()
        self.state.set(WAITING, self._waiting_text())


class HostManager(Manager):
    """The USB host end: switch the port, find the other handheld, connect."""

    def __init__(self, server, info, log, port) -> None:
        super().__init__(server, info, log)
        self.port = port
        self.want = True                # look at once when the app starts
        self.until = 0.0
        self.skip: set[str] = set()     # devices that want authentication
        self.wake = threading.Event()

    def look_again(self) -> None:
        self.want = True
        self.wake.set()

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:
            import traceback
            self.log("link: crash: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
            self.state.set(PROBLEM, "The link part stopped: %s" % e)
        finally:
            self.port.release()

    def _idle(self, text: str) -> None:
        if self.state.status == LINKED or self.state.text != text:
            self.state.set(WAITING, text)

    def _watch(self) -> None:
        """Log what the port and the kernel's USB side do (for after a test)."""
        mode = self.port.mode()
        if mode != self.seen_mode:
            self.log("port: %s%s" % (mode, " (we switched it)" if self.port.forced else ""))
            self.seen_mode = mode
        for line in self.kmsg.read():
            self.log("kernel: " + line)
        usb = adb.usb_listing()
        if usb != self.seen_usb:
            self.log("usb: " + (usb or "nothing plugged in"))
            self.seen_usb = usb
        volts = self.port.vbus()
        if volts is not None and (self.seen_volts is None or abs(volts - self.seen_volts) >= 0.25):
            self.log("port: %.2f V on the USB port's power line" % volts)
            self.seen_volts = volts

    def _run(self) -> None:
        self.kmsg = Kmsg()
        for line in self.kmsg.earlier():
            self.log("kernel (before): " + line)
        self.seen_mode = self.seen_usb = self.seen_volts = None
        started = time.monotonic()
        while not self.stopping.is_set():
            self._watch()
            devs = [d for d in adb.find_devices() if d["sysfs"] not in self.skip]
            plugged = {d["sysfs"] for d in adb.find_devices()}
            self.skip &= plugged
            if devs:
                self._link(devs[0])
                continue
            if self.port.is_host():
                self.port.claim()
                # an OTG adapter makes the port a host by itself; only a host
                # mode we switched on ourselves times out
                if self.port.forced and time.monotonic() > self.until:
                    self.port.release()
                    self._idle("Nothing found. Connect the other handheld with the OTG adapter, then press A")
                else:
                    self._idle("Looking for the other handheld")
            elif self.want and time.monotonic() - started > 3:
                # still a USB device: the adapter didn't switch the port, so do it
                self.want = False
                why = self.port.to_host()
                if why:
                    self.state.set(PROBLEM, why + ". Unplug it, connect the other handheld, then press A")
                else:
                    self.until = time.monotonic() + LOOK_FOR
                    self._idle("Looking for the other handheld")
            elif not self.want:
                self._idle("Connect the other handheld with the OTG adapter, then press A")
            self.wake.wait(0.5)
            self.wake.clear()

    def _link(self, dev: dict) -> None:
        self.until = time.monotonic() + LOOK_FOR
        try:
            t = adb.UsbTransport(dev)
        except OSError as e:
            self._idle("Can't open the other handheld's USB: %s" % (e.strerror or e))
            self.stopping.wait(2)
            return
        link = adb.Link(t, self.log)
        try:
            link.connect()
        except adb.Unauthorized:
            self.log("link: %s wants ADB authentication, leaving it alone" % dev["sysfs"])
            self.skip.add(dev["sysfs"])
            t.close()
            self._idle("The device on the USB port is not a handheld with Handheld Link")
            return
        except (adb.LinkDown, OSError) as e:
            self.log("link: ADB connect failed: %s" % e)
            t.close()
            self.stopping.wait(2)
            return
        self.log("link: ADB up with %s (%s)" % (dev["serial"] or dev["sysfs"], link.banner))
        try:
            self._session(link)
        finally:
            link.close()
        self._watch()
        if not any(d["sysfs"] == dev["sysfs"] for d in adb.find_devices()):
            # unplugged: hand the port back at once, so a PC cable plugged in
            # next never meets this handheld's 5 V
            self.port.release()
            self._idle("The other handheld was unplugged. Connect it again, then press A")

    def _group(self, link: adb.Link, role: str) -> Conn:
        """STREAMS streams to the other handheld's service, as one connection."""
        gid = os.urandom(4).hex()
        socks = []
        try:
            for i in range(STREAMS):
                s = TimedSock(link.open("tcp:%d" % PORT))
                socks.append(s)
                Conn(s).send({"op": "join", "group": gid, "index": i, "of": STREAMS, "role": role})
            rep = Conn(socks[0]).recv()
            if not rep.get("ok"):
                raise PeerError(rep.get("error") or "the other handheld refused the link")
        except BaseException:
            for s in socks:
                s.close()
            raise
        return Conn(Striped(socks))

    def _session(self, link: adb.Link) -> None:
        told = False
        while not self.stopping.is_set():
            if link.dead:
                return
            try:
                conn = self._group(link, "serve")
                break
            except ConnectionRefusedError:
                if not told:
                    self.log("link: the file service is not running there yet")
                    told = True
                self._idle("Open Handheld Link on the other handheld")
            except PeerError as e:
                # an older Handheld Link answers "unknown request 'join'"
                self.log("link: refused: %s" % e)
                self._idle("The other handheld has another version of Handheld Link. "
                           "Put the same version on both")
                self.stopping.wait(5)
                return
            except (socket.timeout, adb.LinkDown, ConnectionError) as e:
                self.log("link: open failed: %s" % e)
                return
            self.stopping.wait(2)
        else:
            return
        try:
            client = Client(conn)
            info = client.call("hello", **self.info)
            back = self._group(link, "reverse")
        except (ConnectionError, PeerError, socket.timeout, OSError) as e:
            self.log("link: hello failed: %s" % e)
            conn.close()
            return
        self._serve(back)
        name = info.get("name") or "the other handheld"
        self.log("link: linked with %s (%s)" % (name, info.get("version")))
        self.state.set(LINKED, "Linked with %s" % name, RemoteSide(client, name), info)
        why = self._keepalive(client, lambda: not link.dead)
        self.log("link: down: %s" % (link.dead or why))
        client.close()
        back.close()
        self._idle("The link went down")


class Kmsg:
    """New kernel messages about USB, without running dmesg."""

    WANT = re.compile(r"usb|musb|vbus|otg|phy|hub|over-?current", re.I)

    def __init__(self) -> None:
        """Starts at the oldest message the kernel still has: earlier()
        gives the USB ones from before (an adapter plugged in first, say)."""
        try:
            self.fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self.fd = None

    def earlier(self, keep: int = 40) -> list[str]:
        return self.read()[-keep:]

    def read(self) -> list[str]:
        out = []
        while self.fd is not None:
            try:
                rec = os.read(self.fd, 4096)
            except BlockingIOError:
                break
            except BrokenPipeError:     # older messages were overwritten
                continue
            except OSError:
                break
            if not rec:
                break
            text = rec.decode("utf-8", "replace").split(";", 1)[-1].split("\n", 1)[0].strip()
            if self.WANT.search(text):
                out.append(text)
        return out


def udc_state() -> str:
    states = []
    for d in sorted(os.listdir("/sys/class/udc")) if os.path.isdir("/sys/class/udc") else []:
        try:
            with open("/sys/class/udc/%s/state" % d) as f:
                states.append(f.read().strip())
        except OSError:
            pass
    return ", ".join(states) or "none"


def adb_running() -> bool:
    """adbd runs and its USB function is mounted (FunKey's ADB mode)."""
    if not os.path.exists("/dev/usb-ffs/adb/ep0"):
        return False
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open("/proc/%s/comm" % pid) as f:
                    if f.read().strip() == "adbd":
                        return True
            except OSError:
                pass
    return False
