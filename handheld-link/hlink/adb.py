"""The ADB host side in pure Python, through usbfs: no adb binary, no libusb.

The handheld that is the USB host talks to the other handheld's adbd (in
ADB mode, FunKey OS has adbd with no authentication). Handheld Link only
needs adbd's tcp:PORT service, which connects a stream to a port on the
other handheld's 127.0.0.1. Streams here behave like sockets: recv(),
sendall(), close().

server_stream() reaches the same services through a PC's adb server, which
is how the file service is tested from a PC.
"""

from __future__ import annotations

import ctypes
import errno
import glob
import os
import socket
import struct
import threading

CNXN, AUTH, OPEN, OKAY = 0x4E584E43, 0x48545541, 0x4E45504F, 0x59414B4F
CLSE, WRTE = 0x45534C43, 0x45545257
VERSION = 0x01000000
MAXDATA = 4096          # old adbd (FunKey's, KNULLI's) takes no more
HDR = struct.Struct("<6I")
BUFFERED = 256 * 1024   # stop acknowledging a stream's data beyond this


class BulkTransfer(ctypes.Structure):
    _fields_ = [("ep", ctypes.c_uint), ("len", ctypes.c_uint),
                ("timeout", ctypes.c_uint), ("data", ctypes.c_void_p)]


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


# the bulk ioctl's number has the struct's size in it: 16 bytes on 32-bit ARM,
# 24 on 64-bit, and ctypes knows which this is
USBDEVFS_BULK = _ioc(3, 2, ctypes.sizeof(BulkTransfer))
USBDEVFS_CLAIMINTERFACE = _ioc(2, 15, 4)
USBDEVFS_RELEASEINTERFACE = _ioc(2, 16, 4)


class LinkDown(ConnectionError):
    pass


class Unauthorized(Exception):
    """The device wants ADB authentication (a phone, say): leave it alone."""


def _read(d: str, name: str) -> str:
    with open(os.path.join(d, name)) as f:
        return f.read().strip()


def find_devices(sysfs: str = "/sys/bus/usb/devices") -> list[dict]:
    """Every ADB interface plugged into this handheld."""
    found = []
    for d in sorted(glob.glob(os.path.join(sysfs, "[0-9]*-*"))):
        base = os.path.basename(d)
        if ":" in base:
            continue
        try:
            for i in sorted(glob.glob(os.path.join(d, base + ":*"))):
                if (_read(i, "bInterfaceClass"), _read(i, "bInterfaceSubClass"),
                        _read(i, "bInterfaceProtocol")) != ("ff", "42", "01"):
                    continue
                eps = {}
                for e in glob.glob(os.path.join(i, "ep_*")):
                    if _read(e, "type") == "Bulk":
                        a = int(_read(e, "bEndpointAddress"), 16)
                        eps["in" if a & 0x80 else "out"] = a
                if len(eps) != 2:
                    continue
                serial = ""
                try:
                    serial = _read(d, "serial")
                except OSError:
                    pass
                found.append({
                    "sysfs": d,
                    "path": "/dev/bus/usb/%03d/%03d" % (int(_read(d, "busnum")), int(_read(d, "devnum"))),
                    "iface": int(_read(i, "bInterfaceNumber"), 16),
                    "ep_in": eps["in"], "ep_out": eps["out"], "serial": serial,
                })
        except (OSError, ValueError):
            continue
    return found


def usb_listing(sysfs: str = "/sys/bus/usb/devices") -> str:
    """One line about every plugged-in USB device (for the log)."""
    out = []
    for d in sorted(glob.glob(os.path.join(sysfs, "[0-9]*-*"))):
        base = os.path.basename(d)
        if ":" in base:
            continue
        try:
            ident = "%s:%s" % (_read(d, "idVendor"), _read(d, "idProduct"))
        except OSError:
            continue
        try:
            ident += " " + _read(d, "product")
        except OSError:
            pass
        ifaces = []
        for i in sorted(glob.glob(os.path.join(d, base + ":*"))):
            try:
                ifaces.append("/".join(_read(i, n) for n in
                                       ("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol")))
            except OSError:
                pass
        out.append("%s %s [%s]" % (base, ident, " ".join(ifaces)))
    return "; ".join(out)


class UsbTransport:
    """ADB messages over a device's bulk endpoints, with exact-length reads."""

    def __init__(self, dev: dict) -> None:
        import fcntl
        self._ioctl = fcntl.ioctl
        self.fd = os.open(dev["path"], os.O_RDWR)
        try:
            self._ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", dev["iface"]))
        except OSError:
            os.close(self.fd)
            raise
        self.iface = dev["iface"]
        self.ep_in, self.ep_out = dev["ep_in"], dev["ep_out"]
        self.rbuf = bytearray(64 * 1024)
        self.rptr = ctypes.addressof((ctypes.c_char * len(self.rbuf)).from_buffer(self.rbuf))
        # one write buffer, reused (under wlock): no new buffer per message
        self.wbuf = bytearray(64 * 1024)
        self.wptr = ctypes.addressof((ctypes.c_char * len(self.wbuf)).from_buffer(self.wbuf))
        self.wlock = threading.Lock()
        self.closed = False

    def _bulk(self, ep: int, addr: int, n: int, timeout_ms: int) -> int:
        req = BulkTransfer(ep, n, timeout_ms, addr)
        return self._ioctl(self.fd, USBDEVFS_BULK, bytearray(req), True)

    def _write(self, data: bytes) -> None:
        n = len(data)
        self.wbuf[:n] = data
        try:
            self._bulk(self.ep_out, self.wptr, n, 5000)
        except OSError as e:
            raise LinkDown("USB write: %s" % e) from None

    def _read_exact(self, n: int, timeout_ms: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            want = min(n - len(out), len(self.rbuf))
            try:
                got = self._bulk(self.ep_in, self.rptr, want, timeout_ms)
            except OSError as e:
                if e.errno == errno.ETIMEDOUT and not self.closed:
                    continue
                raise LinkDown("USB read: %s" % e) from None
            out += self.rbuf[:got]
        return bytes(out)

    def send(self, cmd: int, a0: int, a1: int, data: bytes = b"") -> None:
        if len(data) > len(self.wbuf):
            raise ValueError("ADB payload too big")
        hdr = HDR.pack(cmd, a0, a1, len(data), sum(data) & 0xFFFFFFFF, cmd ^ 0xFFFFFFFF)
        with self.wlock:
            # header and payload go as separate transfers: old adbd reads
            # exactly 24 bytes, then exactly the payload
            self._write(hdr)
            if data:
                self._write(data)

    def recv(self) -> tuple[int, int, int, bytes]:
        # a long timeout: a read the kernel times out can drop a packet that
        # arrives at that moment, and the link is never idle that long (pings)
        cmd, a0, a1, n, _, magic = HDR.unpack(self._read_exact(24, 20000))
        if magic != cmd ^ 0xFFFFFFFF:
            raise LinkDown("not an ADB message")
        return cmd, a0, a1, (self._read_exact(n, 5000) if n else b"")

    def close(self) -> None:
        self.closed = True
        try:
            self._ioctl(self.fd, USBDEVFS_RELEASEINTERFACE, struct.pack("I", self.iface))
        except OSError:
            pass
        os.close(self.fd)


class Stream:
    """One ADB stream, used like a socket."""

    def __init__(self, link: "Link", local: int) -> None:
        self.link = link
        self.local = local
        self.remote = 0
        self.cond = threading.Condition()
        self.opened = False
        self.refused = False
        self.can_write = False
        self.closed = False
        self.buf = bytearray()
        self.owe_okay = False
        self.timeout: float | None = None

    def settimeout(self, t: float | None) -> None:
        self.timeout = t

    # -- called by the link's reader thread --
    def _okay(self, remote: int) -> None:
        with self.cond:
            if not self.opened:
                self.remote = remote
                self.opened = True
            self.can_write = True
            self.cond.notify_all()

    def _data(self, data: bytes) -> bool:
        """Buffer the data; True if it can be acknowledged now."""
        with self.cond:
            self.buf += data
            self.cond.notify_all()
            if len(self.buf) < BUFFERED:
                return True
            self.owe_okay = True
            return False

    def _close(self) -> None:
        with self.cond:
            if not self.opened:
                self.refused = True
            self.closed = True
            self.cond.notify_all()

    # -- the socket side --
    def _wait(self, test) -> None:
        if not self.cond.wait_for(lambda: test() or self.closed, self.timeout):
            raise socket.timeout("timed out")

    def recv(self, n: int) -> bytes:
        with self.cond:
            self._wait(lambda: self.buf)
            if not self.buf:
                return b""
            out = bytes(self.buf[:n])
            del self.buf[:n]
            owe = self.owe_okay and len(self.buf) < BUFFERED // 2
            if owe:
                self.owe_okay = False
        if owe:
            self.link._send(OKAY, self.local, self.remote)
        return out

    def sendall(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            with self.cond:
                self._wait(lambda: self.can_write)
                if self.closed:
                    raise LinkDown("the stream closed")
                self.can_write = False
            part = bytes(view[:self.link.maxdata])
            view = view[len(part):]
            self.link._send(WRTE, self.local, self.remote, part)

    def close(self) -> None:
        with self.cond:
            was = self.closed
            self.closed = True
            self.cond.notify_all()
        if not was and self.opened:
            try:
                self.link._send(CLSE, self.local, self.remote)
            except LinkDown:
                pass
        self.link._forget(self)


class Link:
    """An ADB connection to one device."""

    def __init__(self, transport, log=lambda s: None) -> None:
        self.t = transport
        self.log = log
        self.maxdata = MAXDATA
        self.banner = ""
        self.streams: dict[int, Stream] = {}
        self.lock = threading.Lock()
        self.next_id = 1
        self.dead: str | None = None
        self.on_down = lambda why: None

    def connect(self) -> None:
        self.t.send(CNXN, VERSION, MAXDATA, b"host::\0")
        for _ in range(10):
            cmd, a0, a1, data = self.t.recv()
            if cmd == CNXN:
                self.maxdata = max(256, min(MAXDATA, a1))
                self.banner = data.rstrip(b"\0").decode("utf-8", "replace")
                threading.Thread(target=self._reader, name="adb-reader", daemon=True).start()
                return
            if cmd == AUTH:
                raise Unauthorized("the device wants ADB authentication")
        raise LinkDown("no answer to CONNECT")

    def _send(self, cmd: int, a0: int, a1: int, data: bytes = b"") -> None:
        if self.dead:
            raise LinkDown(self.dead)
        self.t.send(cmd, a0, a1, data)

    def _forget(self, s: Stream) -> None:
        with self.lock:
            self.streams.pop(s.local, None)

    def open(self, service: str, timeout: float = 5.0) -> Stream:
        with self.lock:
            local = self.next_id
            self.next_id += 1
            s = Stream(self, local)
            self.streams[local] = s
        self._send(OPEN, local, 0, service.encode() + b"\0")
        with s.cond:
            if not s.cond.wait_for(lambda: s.opened or s.closed, timeout):
                self._forget(s)
                raise socket.timeout("no answer opening %s" % service)
            if s.refused or not s.opened:
                self._forget(s)
                raise ConnectionRefusedError("%s refused" % service)
        return s

    def _reader(self) -> None:
        try:
            while True:
                cmd, a0, a1, data = self.t.recv()
                with self.lock:
                    s = self.streams.get(a1)
                if cmd == OKAY and s:
                    s._okay(a0)
                elif cmd == WRTE:
                    if s and s._data(data):
                        self._send(OKAY, a1, a0)
                    elif not s:
                        self._send(CLSE, a1, a0)
                elif cmd == CLSE and s:
                    s._close()
                    self._forget(s)
        except (LinkDown, OSError) as e:
            self.dead = str(e) or "the link went down"
        with self.lock:
            streams = list(self.streams.values())
            self.streams.clear()
        for s in streams:
            s._close()
        self.on_down(self.dead)

    def close(self) -> None:
        if not self.dead:
            self.dead = "closed"
        self.t.close()


def server_stream(serial: str, service: str, host: str = "127.0.0.1", port: int = 5037) -> socket.socket:
    """A service on a device, through a PC's adb server (for tests)."""
    s = socket.create_connection((host, port), 5)

    def req(text: str) -> None:
        b = text.encode()
        s.sendall(b"%04x" % len(b) + b)
        status = s.recv(4)
        if status != b"OKAY":
            n = int(s.recv(4) or b"0", 16)
            raise ConnectionRefusedError(s.recv(n).decode(errors="replace") or service)

    req("host:transport:" + serial)
    req(service)
    s.settimeout(None)
    return s
