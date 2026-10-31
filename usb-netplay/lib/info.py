#!/usr/bin/env python3
"""Tell the handheld at the other end of the cable about this one, and ask it.

The info is a short KEY=VALUE text that usbnp writes (firmware, device, USB
Netplay version, netplay core builds, default cores), so the status can
compare the two handhelds before a join fails or desyncs.

usage:
  info.py serve INFO_FILE STATE_DIR   answer on UDP port 55437, but only to
                                      addresses at the other end of a cable
                                      link (STATE_DIR/links/*, gadget-host.*)
  info.py ask ADDRESS [TIMEOUT]       print the other handheld's info
exit 0 on an answer, 1 when nothing answered, 2 on errors.
"""
import os
import socket
import sys

PORT = int(os.environ.get('USBNP_INFO_PORT', '55437'))
QUERY = b'usbnp-info?\n'


def cable_peers(state):
    """Addresses at the other end of this handheld's cable links."""
    peers = {'127.0.0.1'}
    links = os.path.join(state, 'links')
    try:
        names = [os.path.join(links, n) for n in os.listdir(links)]
    except OSError:
        names = []
    try:
        names += [os.path.join(state, n) for n in os.listdir(state) if n.startswith('gadget-host.')]
    except OSError:
        pass
    for path in names:
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('peer='):
                        peers.add(line[5:])
                    elif line and '=' not in line:
                        peers.add(line)   # gadget-host.*: just the address
        except OSError:
            pass
    return peers


def serve(info_file, state, sock=None):
    s = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if sock is None:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', PORT))
    while True:
        data, addr = s.recvfrom(512)
        if data != QUERY or addr[0] not in cable_peers(state):
            continue
        try:
            with open(info_file, 'rb') as f:
                reply = f.read(8000)
        except OSError:
            continue
        s.sendto(reply, addr)


def ask(addr, timeout=1.0, port=PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout / 2)
    try:
        for _ in range(2):
            s.sendto(QUERY, (addr, port))
            try:
                data, src = s.recvfrom(8192)
            except socket.timeout:
                continue
            if src[0] == addr:
                return data.decode('utf-8', 'replace')
    finally:
        s.close()
    return None


def main():
    try:
        if len(sys.argv) >= 4 and sys.argv[1] == 'serve':
            serve(sys.argv[2], sys.argv[3])
        elif len(sys.argv) >= 3 and sys.argv[1] == 'ask':
            info = ask(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 1.0)
            if info is None:
                return 1
            sys.stdout.write(info)
            return 0
    except OSError as e:
        print('info: %s' % e, file=sys.stderr)
        return 2
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
