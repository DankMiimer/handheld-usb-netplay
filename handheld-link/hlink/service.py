"""The file service each handheld runs, the client the other one uses, and
the copy loop that sends items from one handheld to the other.

Requests (J frames), each answered by {"ok": true, ...} or
{"ok": false, "error": "..."}:

    hello                          -> name, device, version, protocol
    ping
    groups  cat                    -> groups: [{id, name, count}]
    items   cat group              -> items: [{id, name}]
    sizes   cat group ids          -> sizes: {id: bytes or null}
    files   cat group item         -> files: [{rel, size, mtime}]
    have    cat group rels         -> have: {rel: {rel, size, mtime}}
    free    cat group              -> free: bytes
    read    cat group rel offset   -> size, mtime, n; then one D frame of n bytes
    write   cat group rel size mtime
            -> ok; then the client sends D frames and E (or X to give up),
               and the server answers again once the file is in place
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Iterator

from . import PROTOCOL, VERSION
from .content import PART, ContentError
from .wire import ABORT, CHUNK, DATA, END, Conn, ProtocolError

SPACE_MARGIN = 1 << 20


class PeerError(Exception):
    """The other handheld refused a request; the text is for the user."""


class Cancelled(Exception):
    pass


# --------------------------------------------------------------- files --
def read_chunks(path: str, offset: int = 0, limit: int | None = None) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(offset)
        left = limit
        while left is None or left > 0:
            b = f.read(CHUNK if left is None else min(CHUNK, left))
            if not b:
                return
            if left is not None:
                left -= len(b)
            yield b


def write_file(path: str, size: int, mtime: int, chunks) -> None:
    """Write to a part file and put it in place only once it is all there."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + PART
    got = 0
    try:
        with open(tmp, "wb") as f:
            for c in chunks:
                got += len(c)
                if got > size:
                    raise ContentError("more data than the file's size")
                f.write(c)
            if got != size:
                raise ContentError("the file came short")
            f.flush()
            os.fsync(f.fileno())
        os.utime(tmp, (mtime, mtime))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Incoming:
    """The D frames of a file being written, up to its E or X."""

    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.done = False

    def __iter__(self):
        while not self.done:
            kind, payload = self.conn.recv_frame()
            if kind == DATA:
                yield payload
            elif kind == END:
                self.done = True
            elif kind == ABORT:
                self.done = True
                raise Cancelled()
            else:
                raise ProtocolError("a message in the middle of a file")

    def drain(self) -> None:
        try:
            for _ in self:
                pass
        except Cancelled:
            pass


# -------------------------------------------------------------- server --
class Server:
    def __init__(self, content, info: dict, log: Callable[[str], None] = lambda s: None) -> None:
        self.content = content
        self.info = info
        self.log = log
        self.note: Callable[[str], None] = lambda s: None

    def serve(self, conn: Conn, first: dict | None = None) -> None:
        """Answer requests until the connection closes."""
        while True:
            if first is not None:
                req, first = first, None
            else:
                try:
                    req = conn.recv()
                except OSError:
                    conn.close()
                    return
            op = req.get("op")
            handler = getattr(self, "op_" + op, None) if isinstance(op, str) else None
            try:
                if handler is None:
                    raise ContentError("unknown request %r" % op)
                handler(conn, req)
            except ConnectionError:
                return
            except (ContentError, OSError, ValueError, KeyError, TypeError) as e:
                self.log("serve %s: %s" % (op, e))
                conn.send({"ok": False, "error": _say(e)})

    def op_hello(self, conn, req):
        conn.send(dict(self.info, ok=True))

    def op_ping(self, conn, req):
        conn.send({"ok": True})

    def op_groups(self, conn, req):
        conn.send({"ok": True, "groups": self.content.groups(req["cat"])})

    def op_items(self, conn, req):
        conn.send({"ok": True, "items": self.content.items(req["cat"], req["group"])})

    def op_sizes(self, conn, req):
        ids = [i for i in req["ids"] if isinstance(i, str)]
        conn.send({"ok": True, "sizes": self.content.sizes(req["cat"], req["group"], ids)})

    def op_files(self, conn, req):
        conn.send({"ok": True, "files": self.content.files(req["cat"], req["group"], req["item"])})

    def op_have(self, conn, req):
        rels = [r for r in req["rels"] if isinstance(r, str)]
        conn.send({"ok": True, "have": self.content.have(req["cat"], req["group"], rels)})

    def op_free(self, conn, req):
        conn.send({"ok": True, "free": self.content.free(req["cat"], req["group"])})

    def op_read(self, conn, req):
        path = self.content.path(req["cat"], req["group"], req["rel"])
        offset = int(req.get("offset", 0))
        st = os.stat(path)
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(CHUNK)
        conn.send({"ok": True, "size": st.st_size, "mtime": int(st.st_mtime), "n": len(data)})
        conn.send_frame(DATA, data)

    def op_write(self, conn, req):
        cat, group, rel = req["cat"], req["group"], req["rel"]
        size, mtime = int(req["size"]), int(req["mtime"])
        path = self.content.path(cat, group, rel)
        if size < 0:
            raise ContentError("bad size")
        if os.path.isdir(path):
            raise ContentError("a folder has that name")
        free = self.content.free(cat, group)
        old = os.path.getsize(path) if os.path.isfile(path) else 0
        if size + SPACE_MARGIN > free + old:
            raise ContentError("not enough space: %s free" % human(free))
        conn.send({"ok": True})
        self.note("Receiving " + rel.rsplit("/", 1)[-1])
        data = Incoming(conn)
        try:
            write_file(path, size, mtime, data)
        except Cancelled:
            conn.send({"ok": False, "error": "cancelled"})
            return
        except ConnectionError:
            raise
        except (ContentError, OSError) as e:
            data.drain()
            self.log("write %s: %s" % (rel, e))
            conn.send({"ok": False, "error": _say(e)})
            return
        self.log("received %s/%s/%s (%d bytes)" % (cat, group, rel, size))
        self.note("Received " + rel.rsplit("/", 1)[-1])
        conn.send({"ok": True})


def _say(e: Exception) -> str:
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(e) or e.__class__.__name__


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%d" % n


# --------------------------------------------------------------- sides --
class LocalSide:
    """This handheld's own files, for the copy loop."""

    def __init__(self, content, name: str) -> None:
        self.content = content
        self.name = name

    def groups(self, cat):
        return self.content.groups(cat)

    def items(self, cat, group):
        return self.content.items(cat, group)

    def sizes(self, cat, group, ids):
        return self.content.sizes(cat, group, ids)

    def files(self, cat, group, item):
        return self.content.files(cat, group, item)

    def have(self, cat, group, rels):
        return self.content.have(cat, group, rels)

    def free(self, cat, group):
        return self.content.free(cat, group)

    def read(self, cat, group, rel):
        return read_chunks(self.content.path(cat, group, rel))

    def write(self, cat, group, rel, size, mtime, chunks):
        path = self.content.path(cat, group, rel)
        write_file(path, size, mtime, chunks)


class Client:
    """Requests to the other handheld, one at a time."""

    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        self.last = time.monotonic()

    def call(self, op: str, **args) -> dict:
        with self.lock:
            return self._call(op, args)

    def _call(self, op, args):
        self.conn.send(dict(args, op=op))
        rep = self.conn.recv()
        self.last = time.monotonic()
        if not rep.get("ok"):
            raise PeerError(rep.get("error") or "refused")
        return rep

    def ping(self, idle: float) -> bool:
        """Ping if nothing went over the link for `idle` seconds; False if the
        link is busy with something else (which shows it is alive too)."""
        if time.monotonic() - self.last < idle or not self.lock.acquire(blocking=False):
            return False
        try:
            self._call("ping", {})
        finally:
            self.lock.release()
        return True

    def close(self) -> None:
        self.conn.close()


class RemoteSide:
    """The other handheld's files, through its file service."""

    def __init__(self, client: Client, name: str) -> None:
        self.client = client
        self.name = name

    def groups(self, cat):
        return self.client.call("groups", cat=cat)["groups"]

    def items(self, cat, group):
        return self.client.call("items", cat=cat, group=group)["items"]

    def sizes(self, cat, group, ids):
        return self.client.call("sizes", cat=cat, group=group, ids=ids)["sizes"]

    def files(self, cat, group, item):
        return self.client.call("files", cat=cat, group=group, item=item)["files"]

    def have(self, cat, group, rels):
        return self.client.call("have", cat=cat, group=group, rels=rels)["have"]

    def free(self, cat, group):
        return self.client.call("free", cat=cat, group=group)["free"]

    def read(self, cat, group, rel):
        offset = 0
        size = None
        while size is None or offset < size:
            with self.client.lock:
                rep = self.client._call("read", dict(cat=cat, group=group, rel=rel, offset=offset))
                kind, data = self.client.conn.recv_frame()
            if kind != DATA or len(data) != rep["n"]:
                raise ProtocolError("a read without its data")
            if size is None:
                size = rep["size"]
            elif rep["size"] != size:
                raise PeerError("%s changed while it was being copied" % rel)
            if not data:
                if offset < size:
                    raise PeerError("%s came short" % rel)
                return
            offset += len(data)
            yield data

    def write(self, cat, group, rel, size, mtime, chunks):
        c = self.client
        with c.lock:
            c._call("write", dict(cat=cat, group=group, rel=rel, size=size, mtime=mtime))
            try:
                for chunk in chunks:
                    c.conn.send_frame(DATA, chunk)
            except Cancelled:
                c.conn.send_frame(ABORT)
                try:
                    c.conn.recv()
                except ConnectionError:
                    pass
                raise
            except ConnectionError:
                raise
            except (OSError, ContentError) as e:
                # reading this side's file failed: the other side drops its part file
                c.conn.send_frame(ABORT)
                c.conn.recv()
                raise PeerError(_say(e)) from None
            c.conn.send_frame(END)
            rep = c.conn.recv()
            c.last = time.monotonic()
            if not rep.get("ok"):
                raise PeerError(rep.get("error") or "refused")


# ---------------------------------------------------------------- copy --
REPLACE, SKIP, REPLACE_ALL, SKIP_ALL, STOP = "replace", "skip", "replace_all", "skip_all", "stop"


class CopyUI:
    """What the copy loop asks of the screen; the defaults never ask."""

    def ask(self, name: str, theirs: dict, ours: list) -> str:
        return SKIP

    def progress(self, name: str, index: int, count: int, done: int, total: int) -> bool:
        """Return False to stop."""
        return True

    def problem(self, text: str) -> None:
        pass


def copy_items(src, dst, cat: str, group: str, items: list[dict], ui: CopyUI) -> dict:
    """Copy items (from items()) from one side to the other.

    Returns {"copied", "skipped", "failed": [(name, why)], "stopped", "bytes"}."""
    result = {"copied": 0, "skipped": 0, "failed": [], "stopped": False, "bytes": 0}
    plan = []
    for it in items:
        try:
            plan.append((it, src.files(cat, group, it["id"])))
        except ConnectionError:
            raise
        except (PeerError, ContentError, OSError) as e:
            result["failed"].append((it["name"], _say(e)))
    total = sum(f["size"] for _, files in plan for f in files)
    try:
        free = dst.free(cat, group)
    except ConnectionError:
        raise
    except (PeerError, ContentError, OSError) as e:
        ui.problem("%s can't take files here: %s" % (dst.name, _say(e)))
        result["stopped"] = True
        return result
    if total + SPACE_MARGIN > free:
        ui.problem("Not enough space on %s: %s needed, %s free" % (dst.name, human(total), human(free)))
        result["stopped"] = True
        return result

    policy = None
    done = 0
    for index, (it, files) in enumerate(plan):
        size = sum(f["size"] for f in files)
        try:
            have = dst.have(cat, group, [f["rel"] for f in files])
        except ConnectionError:
            raise
        except (PeerError, ContentError, OSError) as e:
            result["failed"].append((it["name"], _say(e)))
            done += size
            continue
        if have:
            answer = policy or ui.ask(it["name"], have, files)
            if answer in (REPLACE_ALL, SKIP_ALL):
                policy = answer
            if answer == STOP:
                result["stopped"] = True
                break
            if answer in (SKIP, SKIP_ALL):
                result["skipped"] += 1
                done += size
                continue
        try:
            for f in files:
                def counted(chunks, base=done):
                    sent = 0
                    for c in chunks:
                        if not ui.progress(it["name"], index, len(plan), base + sent, total):
                            raise Cancelled()
                        sent += len(c)
                        yield c
                    ui.progress(it["name"], index, len(plan), base + sent, total)
                dst.write(cat, group, f["rel"], f["size"], f["mtime"],
                          counted(src.read(cat, group, f["rel"])))
                done += f["size"]
                result["bytes"] += f["size"]
            result["copied"] += 1
        except Cancelled:
            result["stopped"] = True
            break
        except ConnectionError:
            raise
        except (PeerError, ContentError, OSError) as e:
            result["failed"].append((it["name"], _say(e)))
            done = sum(f["size"] for _, fs in plan[:index + 1] for f in fs)
    return result


def hello_info(name: str, device: str) -> dict:
    return {"name": name, "device": device, "version": VERSION, "protocol": PROTOCOL}
