#!/usr/bin/env python3
"""Ask the handheld on the other end of a USB network interface for an address.

ROCKNIX's USB network mode runs a DHCP server (udhcpd) that hands the USB host
exactly one address. This sends one DHCPDISCOVER on the interface and prints
the offer as "OFFERED_IP NETMASK SERVER_IP GATEWAY"; it never takes a lease or
changes anything (usbnp sets the address itself, as a /32 without routes or
DNS). GATEWAY is the offered router (or else DNS server), "-" when there is
none: a handheld offers neither, a phone sharing its connection does.
Standard library only, because KNULLI has no busybox udhcpc.

usage: dhcp_probe.py IFACE [TIMEOUT_SECONDS]
exit 0 with one line on stdout, 1 when nothing answered, 2 on errors.
"""
import os
import random
import socket
import struct
import sys
import time

SO_BINDTODEVICE = getattr(socket, 'SO_BINDTODEVICE', 25)
MAGIC = b'\x63\x82\x53\x63'


def discover_packet(mac, xid):
    hdr = struct.pack('!BBBBIHH4s4s4s4s16s64s128s',
                      1, 1, 6, 0, xid, 0, 0x8000,      # BOOTREQUEST, ethernet; ask for a broadcast reply
                      b'\0' * 4, b'\0' * 4, b'\0' * 4, b'\0' * 4,
                      mac.ljust(16, b'\0'), b'', b'')
    opts = bytes([53, 1, 1,             # DHCPDISCOVER
                  55, 4, 1, 3, 6, 54,   # want: subnet mask, router, DNS, server id
                  255])
    return hdr + MAGIC + opts


def parse_offer(data, xid):
    """(yiaddr, netmask, server, gateway) from a DHCPOFFER for xid, or None."""
    if len(data) < 240 or data[0] != 2 or data[236:240] != MAGIC:
        return None
    if struct.unpack('!I', data[4:8])[0] != xid:
        return None
    yiaddr = socket.inet_ntoa(data[16:20])
    opts, i = {}, 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        n = data[i + 1]
        opts[code] = data[i + 2:i + 2 + n]
        i += 2 + n
    if opts.get(53) != b'\x02':
        return None
    mask = socket.inet_ntoa(opts[1]) if len(opts.get(1, b'')) == 4 else '255.255.255.0'
    server = socket.inet_ntoa(opts[54]) if len(opts.get(54, b'')) == 4 else None
    if not server:
        return None
    gateway = '-'
    for code in (3, 6):   # a router, or else a DNS server: a real network
        if len(opts.get(code, b'')) >= 4:
            gateway = socket.inet_ntoa(opts[code][:4])
            break
    return yiaddr, mask, server, gateway


def ip_checksum(hdr):
    s = sum(struct.unpack('!10H', hdr))
    s = (s >> 16) + (s & 0xffff)
    s += s >> 16
    return ~s & 0xffff


def udp_ip_packet(payload):
    """IPv4 + UDP from 0.0.0.0:68 to 255.255.255.255:67 (UDP checksum 0 is allowed on IPv4)"""
    udp = struct.pack('!HHHH', 68, 67, 8 + len(payload), 0) + payload
    hdr = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                      b'\0' * 4, b'\xff' * 4)
    hdr = hdr[:10] + struct.pack('!H', ip_checksum(hdr)) + hdr[12:]
    return hdr + udp


def dhcp_payload_from_ip(pkt):
    """The DHCP payload of an IPv4/UDP packet to port 68, or None."""
    if len(pkt) < 28 or pkt[0] >> 4 != 4 or pkt[9] != 17:
        return None
    ihl = (pkt[0] & 0x0f) * 4
    if len(pkt) < ihl + 8 or struct.unpack('!H', pkt[ihl + 2:ihl + 4])[0] != 68:
        return None
    return pkt[ihl + 8:]


def probe_raw(iface, mac, xid, timeout):
    """Like udhcpc: a raw packet socket, so it works before the interface has an address."""
    ETH_P_IP = 0x0800
    s = socket.socket(socket.AF_PACKET, socket.SOCK_DGRAM, socket.htons(ETH_P_IP))
    s.bind((iface, ETH_P_IP))
    pkt = udp_ip_packet(discover_packet(mac, xid))
    deadline = time.time() + timeout
    next_send = 0
    while time.time() < deadline:
        if time.time() >= next_send:
            s.sendto(pkt, (iface, ETH_P_IP, 0, 0, b'\xff' * 6))
            next_send = time.time() + 1.5
        s.settimeout(max(0.1, min(next_send, deadline) - time.time()))
        try:
            data = s.recv(2048)
        except socket.timeout:
            continue
        payload = dhcp_payload_from_ip(data)
        offer = payload and parse_offer(payload, xid)
        if offer:
            return offer
    return None


def probe(iface, timeout):
    with open('/sys/class/net/%s/address' % iface) as f:
        mac = bytes.fromhex(f.read().strip().replace(':', ''))
    xid = random.getrandbits(32)
    if hasattr(socket, 'AF_PACKET'):
        try:
            return probe_raw(iface, mac, xid, timeout)
        except PermissionError:
            pass   # not root: try a plain UDP socket below
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, iface.encode() + b'\0')
    s.bind(('', 68))
    deadline = time.time() + timeout
    pkt = discover_packet(mac, xid)
    next_send = 0
    while time.time() < deadline:
        if time.time() >= next_send:
            s.sendto(pkt, ('255.255.255.255', 67))
            next_send = time.time() + 1.5
        s.settimeout(max(0.1, min(next_send, deadline) - time.time()))
        try:
            data, _ = s.recvfrom(1500)
        except socket.timeout:
            continue
        offer = parse_offer(data, xid)
        if offer:
            return offer
    return None


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 2
    try:
        offer = probe(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 4)
    except OSError as e:
        print('dhcp_probe: %s' % e, file=sys.stderr)
        return 2
    if not offer:
        return 1
    print('%s %s %s %s' % offer)
    return 0


if __name__ == '__main__':
    sys.exit(main())
