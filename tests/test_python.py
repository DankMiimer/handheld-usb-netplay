"""Python parts of USB Netplay: the DHCP probe and the ADB tunnel + helper.

The tunnel is tested end to end without hardware: a fake old-style adbd
(the semantics of KNULLI's: CNXN, shell:, tcp:, one write in flight per
stream) runs over in-memory queues, and the real helper.py runs on loopback.
"""
import os
import queue
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'usb-netplay')
sys.path.insert(0, os.path.join(SRC, 'lib'))
sys.path.insert(0, os.path.join(SRC, 'adb'))

# ports used by this test only, so a real RetroArch or helper can't interfere
os.environ.update(USBNP_NETPLAY_PORT='57435', USBNP_HELPER_PORT='57439', USBNP_JOIN_PORT='56435')

import dhcp_probe   # noqa: E402
import tunnel       # noqa: E402

A_CNXN, A_OPEN, A_OKAY, A_CLSE, A_WRTE, A_AUTH = (
    tunnel.A_CNXN, tunnel.A_OPEN, tunnel.A_OKAY, tunnel.A_CLSE, tunnel.A_WRTE, tunnel.A_AUTH)


class DhcpProbeTest(unittest.TestCase):
    def offer(self, xid, yiaddr='10.171.9.1', server='10.171.9.2', kind=2):
        pkt = bytearray(240)
        pkt[0] = 2
        pkt[4:8] = struct.pack('!I', xid)
        pkt[16:20] = socket.inet_aton(yiaddr)
        pkt[236:240] = dhcp_probe.MAGIC
        pkt += bytes([53, 1, kind, 1, 4]) + socket.inet_aton('255.255.255.0')
        pkt += bytes([54, 4]) + socket.inet_aton(server) + bytes([255])
        return bytes(pkt)

    def test_discover_packet(self):
        p = dhcp_probe.discover_packet(bytes.fromhex('0211223344aa'), 0x12345678)
        self.assertEqual(p[0], 1)
        self.assertEqual(struct.unpack('!I', p[4:8])[0], 0x12345678)
        self.assertEqual(struct.unpack('!H', p[10:12])[0], 0x8000)   # broadcast reply wanted
        self.assertEqual(p[28:34], bytes.fromhex('0211223344aa'))
        self.assertIn(bytes([53, 1, 1]), p[240:])

    def test_parse_offer(self):
        self.assertEqual(dhcp_probe.parse_offer(self.offer(7), 7), ('10.171.9.1', '255.255.255.0', '10.171.9.2'))

    def test_raw_packet(self):
        payload = dhcp_probe.discover_packet(bytes(6), 1)
        ip = dhcp_probe.udp_ip_packet(payload)
        self.assertEqual(ip[0], 0x45)
        self.assertEqual(dhcp_probe.ip_checksum(ip[:10] + b'\0\0' + ip[12:20]), struct.unpack('!H', ip[10:12])[0])
        self.assertEqual(struct.unpack('!HH', ip[20:24]), (68, 67))
        # an offer coming back the same way is found again
        reply = bytearray(dhcp_probe.udp_ip_packet(self.offer(9)))
        reply[20:24] = struct.pack('!HH', 67, 68)
        self.assertEqual(dhcp_probe.parse_offer(dhcp_probe.dhcp_payload_from_ip(bytes(reply)), 9)[2], '10.171.9.2')
        self.assertIsNone(dhcp_probe.dhcp_payload_from_ip(bytes(ip)))   # our own discover (to port 67) is ignored

    def test_ignores_others(self):
        self.assertIsNone(dhcp_probe.parse_offer(self.offer(7), 8))          # another transaction
        self.assertIsNone(dhcp_probe.parse_offer(self.offer(7, kind=5), 7))  # an ACK, not an offer
        self.assertIsNone(dhcp_probe.parse_offer(b'\0' * 100, 7))


class FakeAdbd(threading.Thread):
    """An old adbd behind a USB cable, over two queues."""

    def __init__(self, auth=False):
        super().__init__(daemon=True)
        self.to_host, self.from_host = queue.Queue(), queue.Queue()
        self.streams, self.next_id, self.auth, self.closed = {}, 100, auth, False

    def send(self, cmd, a0, a1, data=b''):
        self.to_host.put((cmd, a0, a1, data))

    def unplug(self):
        self.closed = True
        for st in list(self.streams.values()):
            if st.get('sock'):
                try:
                    st['sock'].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                st['sock'].close()

    def run(self):
        while not self.closed:
            try:
                cmd, a0, a1, data = self.from_host.get(timeout=0.2)
            except queue.Empty:
                continue
            if cmd == A_CNXN:
                if self.auth:
                    self.send(A_AUTH, 1, 0, b'token')
                else:
                    self.send(A_CNXN, 0x01000000, 4096, b'device::fake\0')
            elif cmd == A_OPEN:
                rid, self.next_id = self.next_id, self.next_id + 1
                svc = data.rstrip(b'\0').decode()
                st = {'local': a0, 'credit': threading.Event()}
                st['credit'].set()
                self.streams[rid] = st
                if svc.startswith('shell:'):
                    threading.Thread(target=self.shell, args=(rid, st, svc[6:]), daemon=True).start()
                elif svc.startswith('tcp:'):
                    try:
                        st['sock'] = socket.create_connection(('127.0.0.1', int(svc[4:])), timeout=2)
                    except OSError:
                        del self.streams[rid]
                        self.send(A_CLSE, 0, a0)
                        continue
                    self.send(A_OKAY, rid, a0)
                    threading.Thread(target=self.reader, args=(rid, st), daemon=True).start()
            elif cmd == A_OKAY:
                st = self.streams.get(a1)
                if st:
                    st['credit'].set()
            elif cmd == A_WRTE:
                st = self.streams.get(a1)
                if st and st.get('sock'):
                    st['sock'].sendall(data)
                self.send(A_OKAY, a1, a0)
            elif cmd == A_CLSE:
                st = self.streams.pop(a1, None)
                if st and st.get('sock'):
                    st['sock'].close()

    def write(self, rid, st, data):
        st['credit'].wait(5)
        st['credit'].clear()
        self.send(A_WRTE, rid, st['local'], data)

    def shell(self, rid, st, cmd):
        self.send(A_OKAY, rid, st['local'])
        out = subprocess.run(['/bin/sh', '-c', cmd], capture_output=True).stdout
        if out:
            self.write(rid, st, out)
        self.streams.pop(rid, None)
        self.send(A_CLSE, rid, st['local'])

    def reader(self, rid, st):
        try:
            while True:
                data = st['sock'].recv(4096)
                if not data:
                    break
                self.write(rid, st, data)
        except OSError:
            pass
        if self.streams.pop(rid, None) is not None:
            self.send(A_CLSE, rid, st['local'])


class FakeTransport:
    def __init__(self, adbd):
        self.adbd = adbd
        self.closed = False

    def send(self, cmd, a0, a1, data=b''):
        if self.closed or self.adbd.closed:
            raise tunnel.Gone('unplugged')
        self.adbd.from_host.put((cmd, a0, a1, data))

    def recv(self):
        while True:
            if self.closed or self.adbd.closed:
                raise tunnel.Gone('unplugged')
            try:
                return self.adbd.to_host.get(timeout=0.2)
            except queue.Empty:
                pass


def echo_server(port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(4)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=lambda c=c: [c.sendall(d) for d in iter(lambda: c.recv(4096), b'')], daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return srv


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


class TunnelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hosts_log = os.path.join(self.tmp, 'hosts.log')
        stub = os.path.join(self.tmp, 'hosts.sh')
        with open(stub, 'w') as f:
            f.write('#!/bin/sh\necho "$1" >> %s\n' % self.hosts_log)
        os.environ['USBNP_HOSTS_CMD'] = '/bin/sh ' + stub
        os.environ['USBNP_HELPER_LOG'] = os.path.join(self.tmp, 'helper.log')
        self.saved_paths = tunnel.HELPER_PATHS
        tunnel.HELPER_PATHS = [os.path.join(SRC, 'adb', 'helper.py')]

    def tearDown(self):
        tunnel.HELPER_PATHS = self.saved_paths
        subprocess.call(['pkill', '-f', os.path.join(SRC, 'adb', 'helper.py')])

    def hosts(self):
        try:
            with open(self.hosts_log) as f:
                return f.read().split()
        except OSError:
            return []

    def run_session(self, adbd):
        link = tunnel.Link(FakeTransport(adbd))
        result = {}

        def go():
            try:
                tunnel.session(link)
            except Exception as e:   # the test inspects whatever ended it
                result['error'] = e
        t = threading.Thread(target=go, daemon=True)
        t.start()
        return t, result

    def test_join_goes_through_the_tunnel(self):
        srv = echo_server(57435)          # stands in for this handheld's RetroArch host
        adbd = FakeAdbd()
        adbd.start()
        t, result = self.run_session(adbd)
        self.assertTrue(wait_for(lambda: 'up' in self.hosts()), 'helper never came up')
        c = socket.create_connection(('127.0.0.1', 56435), timeout=5)   # the other handheld's RetroArch joining
        c.sendall(b'netplay hello')
        got = b''
        c.settimeout(5)
        while len(got) < 13:
            got += c.recv(100)
        self.assertEqual(got, b'netplay hello')
        blob = os.urandom(200000)
        c.sendall(blob)
        got = b''
        while len(got) < len(blob):
            got += c.recv(65536)
        self.assertEqual(got, blob, '200 kB came back changed')
        c.close()
        adbd.unplug()                      # cable out
        t.join(10)
        self.assertIsInstance(result.get('error'), tunnel.Gone)
        self.assertTrue(wait_for(lambda: 'down' in self.hosts(), 30), 'usb-netplay not put back after unplug')
        srv.close()

    def test_phone_is_left_alone(self):
        adbd = FakeAdbd(auth=True)
        adbd.start()
        with self.assertRaises(tunnel.Skip):
            tunnel.session(tunnel.Link(FakeTransport(adbd)))
        adbd.unplug()

    def test_device_without_usb_netplay_is_left_alone(self):
        tunnel.HELPER_PATHS = [os.path.join(self.tmp, 'nothing-here.py')]
        adbd = FakeAdbd()
        adbd.start()
        with self.assertRaises(tunnel.Skip):
            tunnel.session(tunnel.Link(FakeTransport(adbd)))
        adbd.unplug()
        self.assertEqual(self.hosts(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
