#!/usr/bin/env python3
"""USB Netplay's ADB tunnel: netplay with a handheld that offers only ADB.

Some firmwares (KNULLI on H700/A133) have no USB network gadget, only ADB, and
their adbd is too old for "adb reverse". When such a handheld is plugged into
this one (this one is the USB host), this talks ADB to it directly through
usbfs, starts USB Netplay's helper on it (adb/helper.py, installed there by
USB Netplay) and keeps a control connection to the helper (adbd's
"tcp:55439" service). When the other handheld's RetroArch joins (it connects
to 127.0.0.1:55435 there), the helper asks for a data connection and this
connects it to this handheld's RetroArch host on 127.0.0.1:55435.

It only ever touches a device that (1) has an ADB interface, (2) lets in an
unauthenticated host (phones ask for authorisation and are skipped), and
(3) has USB Netplay's helper installed. Standard library only. Run by
"usbnp watch"; it waits for a device and reconnects after every replug.
"""
import ctypes
import errno
import fcntl
import glob
import os
import socket
import struct
import sys
import threading
import time

A_CNXN, A_OPEN, A_OKAY = 0x4e584e43, 0x4e45504f, 0x59414b4f
A_CLSE, A_WRTE, A_AUTH = 0x45534c43, 0x45545257, 0x48545541
USBDEVFS_BULK = 0xC0185502            # _IOWR('U', 2, struct usbdevfs_bulktransfer), 64-bit
USBDEVFS_CLAIMINTERFACE = 0x8004550F  # _IOR('U', 15, unsigned int)
NETPLAY_PORT = int(os.environ.get('USBNP_NETPLAY_PORT', 55435))   # RetroArch host here
HELPER_PORT = int(os.environ.get('USBNP_HELPER_PORT', 55439))     # helper on the other handheld
PING_EVERY = 5
NO_HELPER = 'USBNP-NO-HELPER'
# where USB Netplay installs itself on each firmware (see lib/detect.sh)
HELPER_PATHS = [
    '/userdata/system/usb-netplay/adb/helper.py',   # KNULLI, Batocera
    '/storage/.config/usb-netplay/adb/helper.py',   # ROCKNIX, AmberELEC
    '/opt/usb-netplay/adb/helper.py',               # dArkOS, ArkOS
]

_log_lock = threading.Lock()


def log(msg):
    with _log_lock:   # one write per line, so threads can't run lines together
        sys.stdout.write(time.strftime('%F %T ') + 'tunnel: ' + msg + '\n')
        sys.stdout.flush()


class Gone(Exception):
    pass


class Skip(Exception):
    """This device must be left alone until it is unplugged."""


def sysread(d, name):
    with open(os.path.join(d, name)) as f:
        return f.read().strip()


def find_adb_devices():
    """[(sysfs dir, usbfs path, interface number, bulk IN, bulk OUT)] for every ADB interface"""
    found = []
    for d in sorted(glob.glob('/sys/bus/usb/devices/[0-9]*-*')):
        base = os.path.basename(d)
        if ':' in base:
            continue
        try:
            for i in glob.glob(d + '/' + base + ':*'):
                if (sysread(i, 'bInterfaceClass'), sysread(i, 'bInterfaceSubClass'), sysread(i, 'bInterfaceProtocol')) != ('ff', '42', '01'):
                    continue
                eps = {}
                for e in glob.glob(i + '/ep_*'):
                    if sysread(e, 'type') == 'Bulk':
                        a = int(sysread(e, 'bEndpointAddress'), 16)
                        eps['in' if a & 0x80 else 'out'] = a
                if len(eps) == 2:
                    path = '/dev/bus/usb/%03d/%03d' % (int(sysread(d, 'busnum')), int(sysread(d, 'devnum')))
                    found.append((d, path, int(sysread(i, 'bInterfaceNumber'), 16), eps['in'], eps['out']))
        except (OSError, ValueError):
            continue
    return found


class UsbTransport:
    """ADB messages over usbfs bulk endpoints, with exact-length reads like adb itself."""

    def __init__(self, path, iface, ep_in, ep_out):
        self.fd = os.open(path, os.O_RDWR)
        fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack('I', iface))
        self.ep_in, self.ep_out = ep_in, ep_out
        self.rbuf = ctypes.create_string_buffer(256 * 1024)
        self.wlock = threading.Lock()
        self.closed = False

    def _bulk(self, ep, buf, n, timeout_ms):
        req = bytearray(struct.pack('IIIIQ', ep, n, timeout_ms, 0, ctypes.addressof(buf)))
        return fcntl.ioctl(self.fd, USBDEVFS_BULK, req, True)

    def _write(self, data):
        buf = ctypes.create_string_buffer(data, len(data))
        try:
            self._bulk(self.ep_out, buf, len(data), 5000)
        except OSError as e:
            raise Gone('USB write: %s' % e)

    def _read_exact(self, n):
        out = b''
        while len(out) < n:
            want = min(n - len(out), len(self.rbuf))
            try:
                got = self._bulk(self.ep_in, self.rbuf, want, 1000)
            except OSError as e:
                if e.errno == errno.ETIMEDOUT and not self.closed:
                    continue
                raise Gone('USB read: %s' % e)
            out += self.rbuf.raw[:got]
        return out

    def send(self, cmd, a0, a1, data=b''):
        hdr = struct.pack('<6I', cmd, a0, a1, len(data), sum(data) & 0xffffffff, cmd ^ 0xffffffff)
        with self.wlock:
            self._write(hdr)
            if data:
                self._write(data)

    def recv(self):
        cmd, a0, a1, n, _, magic = struct.unpack('<6I', self._read_exact(24))
        if magic != cmd ^ 0xffffffff:
            raise Gone('bad ADB header')
        return cmd, a0, a1, (self._read_exact(n) if n else b'')

    def close(self):
        self.closed = True
        os.close(self.fd)


class Stream:
    def __init__(self, link, local_id):
        self.link, self.local_id, self.remote_id = link, local_id, 0
        self.opened = threading.Event()
        self.credit = threading.Event()   # set when the device has acknowledged our last write
        self.closed = threading.Event()
        self.on_data = None
        self.on_close = None

    def write(self, data):
        step = self.link.maxdata
        for off in range(0, len(data), step):
            if not self.credit.wait(15) or self.closed.is_set():
                raise OSError('ADB stream closed')
            self.credit.clear()
            self.link.t.send(A_WRTE, self.local_id, self.remote_id, data[off:off + step])

    def close(self):
        if not self.closed.is_set():
            self.closed.set()
            self.credit.set()
            try:
                self.link.t.send(A_CLSE, self.local_id, self.remote_id)
            except Gone:
                pass
            self.link.streams.pop(self.local_id, None)


class Link:
    def __init__(self, transport):
        self.t = transport
        self.streams = {}
        self.next_id = 1
        self.lock = threading.Lock()
        self.maxdata = 4096
        self.error = 'reader stopped'

    def connect(self):
        self.t.send(A_CNXN, 0x01000000, 4096, b'host::\0')
        deadline = time.time() + 10
        while time.time() < deadline:
            cmd, _, a1, data = self.t.recv()
            if cmd == A_CNXN:
                self.maxdata = min(4096, a1)
                return data.rstrip(b'\0').decode(errors='replace')
            if cmd == A_AUTH:
                raise Skip('it asks for ADB authorisation (a phone or a locked device), leaving it alone')
        raise Gone('no answer to CNXN')

    def open(self, service, on_data=None, timeout=5):
        with self.lock:
            s = Stream(self, self.next_id)
            self.next_id += 1
        s.on_data = on_data
        self.streams[s.local_id] = s
        self.t.send(A_OPEN, s.local_id, 0, service.encode() + b'\0')
        if not s.opened.wait(timeout) or s.closed.is_set():
            self.streams.pop(s.local_id, None)
            raise OSError('ADB open %s refused' % service.split(':')[0])
        return s

    def run(self):
        """Reader loop; ends (by raising Gone) when the device goes away."""
        while True:
            cmd, a0, a1, data = self.t.recv()
            s = self.streams.get(a1)
            if cmd == A_OKAY and s:
                if not s.opened.is_set():
                    s.remote_id = a0
                    s.opened.set()
                s.credit.set()
            elif cmd == A_WRTE and s:
                if s.on_data:
                    try:
                        s.on_data(data)
                    except OSError:
                        s.close()
                        continue
                self.t.send(A_OKAY, s.local_id, s.remote_id)
            elif cmd == A_CLSE:
                if s:
                    self.streams.pop(a1, None)
                    s.closed.set()
                    s.opened.set()
                    s.credit.set()
                    if s.on_close:
                        s.on_close()
            elif cmd == A_CNXN:
                raise Gone('the other handheld\'s adbd restarted')

    def drop_all(self):
        for s in list(self.streams.values()):
            s.closed.set()
            s.opened.set()
            s.credit.set()
            if s.on_close:
                s.on_close()
        self.streams.clear()


def shut(sock):
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def start_helper(link):
    """Start the helper on the other handheld; Skip if USB Netplay isn't installed there."""
    out = bytearray()
    cmd = 'for f in %s; do [ -f "$f" ] && exec python3 "$f" --daemon; done; echo %s' % (
        ' '.join(HELPER_PATHS), NO_HELPER)
    sh = link.open('shell:' + cmd, on_data=out.extend)
    sh.closed.wait(10)
    if NO_HELPER.encode() in out:
        raise Skip('USB Netplay is not installed on it')


def data_connection(link, cid):
    """The other handheld's RetroArch is joining: connect it to the RetroArch host here."""
    try:
        local = socket.create_connection(('127.0.0.1', NETPLAY_PORT), timeout=3)
        local.settimeout(None)
        local.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        local = None
        log('join %d: nothing is hosting on port %d here' % (cid, NETPLAY_PORT))
    try:
        s = link.open('tcp:%d' % HELPER_PORT, on_data=local.sendall if local else None)
        s.on_close = (lambda: shut(local)) if local else None
        s.write(b'DATA %d\n' % cid)
    except (OSError, Gone):
        if local:
            local.close()
        return
    if not local:
        s.close()
        return
    log('join %d: connected to the RetroArch host' % cid)
    try:
        while True:
            data = local.recv(65536)
            if not data:
                break
            s.write(data)
    except (OSError, Gone):
        pass
    s.close()
    local.close()
    log('join %d: closed' % cid)


def _run(link):
    try:
        link.run()
    except Gone as e:
        link.error = str(e)
    except Exception as e:   # keep the loop alive whatever happens in the reader
        link.error = 'reader: %r' % e


def session(link):
    """Serve one connected device until it goes away."""
    log('connected to adbd on the other handheld: %s' % link.connect()[:60])
    reader = threading.Thread(target=_run, args=(link,), daemon=True)
    reader.start()
    start_helper(link)
    ctrl, buf = None, bytearray()

    def on_ctrl(data):
        buf.extend(data)
        while b'\n' in buf:
            i = buf.index(b'\n')
            line = bytes(buf[:i]).decode(errors='replace')
            del buf[:i + 1]
            if line.startswith('OPEN '):
                threading.Thread(target=data_connection, args=(link, int(line[5:])), daemon=True).start()
            elif line == 'READY':
                log('helper ready: the other handheld can join on 127.0.0.1:%d' % NETPLAY_PORT)
            elif line == 'BUSY':
                log('helper could not listen there (is RetroArch hosting on that handheld?)')

    for _ in range(20):
        try:
            ctrl = link.open('tcp:%d' % HELPER_PORT, on_data=on_ctrl)
            break
        except OSError:
            time.sleep(0.5)
    if not ctrl:
        raise Gone('the helper on the other handheld did not start')
    ctrl.write(b'CTRL\n')
    while reader.is_alive() and not ctrl.closed.is_set():
        reader.join(PING_EVERY)
        if reader.is_alive():
            ctrl.write(b'PING\n')
    if not reader.is_alive():
        raise Gone(link.error)
    log('control connection closed')


def main():
    log('waiting for an ADB-only handheld on the host port')
    skipped = {}   # sysfs dir -> device number when skipped (cleared on replug)
    while True:
        for d, path, iface, ep_in, ep_out in find_adb_devices():
            try:
                devnum = sysread(d, 'devnum')
            except OSError:
                continue
            if skipped.get(d) == devnum:
                continue
            transport = None
            try:
                transport = UsbTransport(path, iface, ep_in, ep_out)
                link = Link(transport)
                try:
                    session(link)
                finally:
                    link.drop_all()
            except Skip as e:
                log('%s: %s' % (os.path.basename(d), e))
                skipped[d] = devnum
            except (Gone, OSError) as e:
                log('tunnel down: %s' % e)
            finally:
                if transport:
                    transport.closed = True
                    transport.close()
            time.sleep(3)
        for d in list(skipped):
            if not os.path.isdir(d):
                del skipped[d]
        time.sleep(2)


if __name__ == '__main__':
    main()
