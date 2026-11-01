"""Framing for the file service, over any socket-like stream.

A frame is a 1-byte kind, a 4-byte big-endian length and the payload:
J (a JSON message), D (file data), E (end of a file's data) or X (the sender
gave up on the file). Requests and replies are J frames; a file being
written follows its request as D frames and an E.
"""

from __future__ import annotations

import json
import queue
import struct
import threading

HDR = struct.Struct(">cI")
JSON, DATA, END, ABORT = b"J", b"D", b"E", b"X"
MAX_FRAME = 8 << 20
CHUNK = 256 * 1024


class ProtocolError(ConnectionError):
    """The other side sent something that is not this protocol."""


class Conn:
    def __init__(self, sock) -> None:
        self.sock = sock

    def _exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            b = self.sock.recv(n - len(buf))
            if not b:
                raise ConnectionError("the link closed")
            buf += b
        return bytes(buf)

    def send_frame(self, kind: bytes, payload: bytes = b"") -> None:
        if len(payload) <= 4096:
            self.sock.sendall(HDR.pack(kind, len(payload)) + payload)
        else:
            self.sock.sendall(HDR.pack(kind, len(payload)))
            self.sock.sendall(payload)

    def recv_frame(self) -> tuple[bytes, bytes]:
        kind, n = HDR.unpack(self._exact(HDR.size))
        if kind not in (JSON, DATA, END, ABORT) or n > MAX_FRAME:
            raise ProtocolError("not a Handheld Link frame")
        return kind, (self._exact(n) if n else b"")

    def send(self, msg: dict) -> None:
        self.send_frame(JSON, json.dumps(msg, separators=(",", ":")).encode())

    def recv(self) -> dict:
        kind, payload = self.recv_frame()
        if kind != JSON:
            raise ProtocolError("expected a message")
        try:
            msg = json.loads(payload)
        except ValueError:
            raise ProtocolError("a message that is not JSON") from None
        if not isinstance(msg, dict):
            raise ProtocolError("a message that is not an object")
        return msg

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Striped:
    """One byte stream over several sockets, used like one socket.

    Over old adbd every 4 KB on a stream waits for its acknowledgement, so
    one stream carries about 1 MB/s. Here piece n of the stream goes on
    socket n % K: K pieces travel at once, and the other end reads them back
    in order, so files are still written front to back."""

    PIECE = 16 * 1024
    LEN = struct.Struct(">I")

    def __init__(self, socks: list) -> None:
        self.socks = socks
        self.k = len(socks)
        self.queues = [queue.Queue(maxsize=4) for _ in socks]
        self.error: Exception | None = None
        self.closed = False
        self.sent = 0
        self.got = 0
        self.cur = b""
        self.lock = threading.Lock()
        for i in range(self.k):
            threading.Thread(target=self._sender, args=(i,), daemon=True, name="striped-%d" % i).start()

    def _fail(self, e: Exception) -> None:
        if self.error is None:
            self.error = e
        self.close()

    def _sender(self, i: int) -> None:
        q, s = self.queues[i], self.socks[i]
        while not self.closed:
            try:
                piece = q.get(timeout=1)
            except queue.Empty:
                continue
            if piece is None:
                return
            try:
                s.sendall(piece)
            except Exception as e:
                self._fail(e)
                return

    def sendall(self, data) -> None:
        view = memoryview(data)
        with self.lock:
            for start in range(0, len(view), self.PIECE):
                part = view[start:start + self.PIECE]
                piece = self.LEN.pack(len(part)) + bytes(part)
                q = self.queues[self.sent % self.k]
                while True:
                    if self.error or self.closed:
                        raise ConnectionError(str(self.error or "the link closed"))
                    try:
                        q.put(piece, timeout=1)
                        break
                    except queue.Full:
                        continue
                self.sent += 1

    def _exact(self, s, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            b = s.recv(n - len(buf))
            if not b:
                raise ConnectionError(str(self.error or "the link closed"))
            buf += b
        return bytes(buf)

    def recv(self, n: int) -> bytes:
        if not self.cur:
            s = self.socks[self.got % self.k]
            try:
                size = self.LEN.unpack(self._exact(s, 4))[0]
                self.cur = self._exact(s, size)
            except ConnectionError:
                raise
            except OSError as e:
                self._fail(e)
                raise ConnectionError(str(e)) from None
            self.got += 1
        out, self.cur = self.cur[:n], self.cur[n:]
        return out

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for s in self.socks:
            try:
                s.close()
            except OSError:
                pass
