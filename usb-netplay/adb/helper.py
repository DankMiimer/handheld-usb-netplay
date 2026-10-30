#!/usr/bin/env python3
"""USB Netplay's helper on a handheld that offers only ADB (see tunnel.py).

The USB host on the other end of the cable starts this over ADB and keeps a
control connection to it (adbd's "tcp:55439" service). It listens on
127.0.0.1:55435 for this handheld's RetroArch joining the other handheld's
game; for each connection it asks the tunnel (over the control connection)
to open a data connection, and pairs the two. While it runs, the name
usb-netplay (RetroArch's pre-filled netplay host) points at 127.0.0.1; when
the control connection goes away (cable out, tunnel stopped, no ping for
20 s) it points usb-netplay back and exits.
"""
import os
import select
import socket
import subprocess
import sys
import threading
import time

JOIN_PORT = int(os.environ.get('USBNP_JOIN_PORT', 55435))
HELPER_PORT = int(os.environ.get('USBNP_HELPER_PORT', 55439))
PING_TIMEOUT = 20
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS = os.environ.get('USBNP_HOSTS_CMD', '/bin/sh %s/usbnp hosts-adb' % HERE)
LOG = os.environ.get('USBNP_HELPER_LOG', '/tmp/usb-netplay-helper.log')


def log(msg):
    with open(LOG, 'a') as f:
        f.write(time.strftime('%F %T ') + msg + '\n')


def hosts(state):
    subprocess.call(HOSTS.split() + [state])


def listener(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', port))
    s.listen(8)
    return s


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    for s in (src, dst):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def relay(a, b):
    for s in (a, b):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    t = threading.Thread(target=pipe, args=(b, a), daemon=True)
    t.start()
    pipe(a, b)
    t.join()
    a.close()
    b.close()


def read_line(s, timeout=5):
    s.settimeout(timeout)
    line = b''
    while not line.endswith(b'\n') and len(line) < 64:
        c = s.recv(1)
        if not c:
            break
        line += c
    s.settimeout(None)
    return line.decode(errors='replace').strip()


def main():
    try:
        tun = listener(HELPER_PORT)
    except OSError:
        return  # another helper is already running
    tun.settimeout(30)
    try:
        ctrl, _ = tun.accept()
    except OSError:
        log('the tunnel never connected')
        return
    tun.settimeout(None)
    if read_line(ctrl) != 'CTRL':
        return
    try:
        net = listener(JOIN_PORT)
    except OSError as e:
        log('can\'t listen on %d (is RetroArch hosting here?): %s' % (JOIN_PORT, e))
        ctrl.sendall(b'BUSY\n')
        return
    ctrl.sendall(b'READY\n')
    hosts('up')
    log('tunnel up')
    pending = {}   # id -> (client socket, time)
    next_id = 1
    last_ping = last_check = time.time()
    buf = b''
    try:
        while True:
            r, _, _ = select.select([ctrl, net, tun], [], [], 2)
            now = time.time()
            if ctrl in r:
                data = ctrl.recv(4096)
                if not data:
                    log('control connection closed')
                    break
                buf += data
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    if line == b'PING':
                        last_ping = now
            if net in r:
                client, _ = net.accept()
                pending[next_id] = (client, now)
                ctrl.sendall(b'OPEN %d\n' % next_id)
                next_id += 1
            if tun in r:
                conn, _ = tun.accept()
                try:
                    words = read_line(conn).split()
                except OSError:
                    words = []
                if len(words) == 2 and words[0] == 'DATA' and words[1].isdigit() and int(words[1]) in pending:
                    client, _ = pending.pop(int(words[1]))
                    threading.Thread(target=relay, args=(client, conn), daemon=True).start()
                else:
                    conn.close()
            for i, (client, t) in list(pending.items()):
                if now - t > 10:
                    client.close()
                    del pending[i]
            if now - last_ping > PING_TIMEOUT:
                log('no ping from the tunnel for %d s' % PING_TIMEOUT)
                break
            if now - last_check > 10:
                # a boot step can rewrite the hosts file after the tunnel came up
                last_check = now
                hosts('up')
    finally:
        hosts('down')
        log('tunnel down')


if __name__ == '__main__':
    if '--daemon' in sys.argv:
        if os.fork():
            sys.exit(0)
        os.setsid()
        if os.fork():
            sys.exit(0)
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
    main()
