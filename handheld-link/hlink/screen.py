"""Drawing on the Linux framebuffer and reading the buttons, without SDL.

The drawing area is the part of the framebuffer the panel shows (all of it,
or the GBA Mini's middle 160 rows), and the font is picked from its size so
the same screens fit 240x160 up to 640x480 and beyond. Text is monospace.
With no framebuffer (HLINK_HEADLESS=WxH) it draws into memory and can save
a PNG, for previews and tests on a PC.
"""

from __future__ import annotations

import ctypes
import os
import select
import struct
import time
import unicodedata
import zlib

from .font import FONTS

FBIOGET_VSCREENINFO, FBIOGET_FSCREENINFO, FBIOPAN_DISPLAY = 0x4600, 0x4602, 0x4606
KDSETMODE, KDGETMODE, KD_GRAPHICS = 0x4B3A, 0x4B3B, 0x01


class FixInfo(ctypes.Structure):
    _fields_ = [("id", ctypes.c_char * 16), ("smem_start", ctypes.c_ulong),
                ("smem_len", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("type_aux", ctypes.c_uint32), ("visual", ctypes.c_uint32),
                ("xpanstep", ctypes.c_uint16), ("ypanstep", ctypes.c_uint16),
                ("ywrapstep", ctypes.c_uint16), ("line_length", ctypes.c_uint32),
                ("mmio_start", ctypes.c_ulong), ("mmio_len", ctypes.c_uint32),
                ("accel", ctypes.c_uint32), ("capabilities", ctypes.c_uint16),
                ("reserved", ctypes.c_uint16 * 2)]


def pick_font(w: int, h: int) -> str:
    """The biggest font that still gives about 38 columns and 12 lines."""
    want = os.environ.get("HLINK_FONT")
    if want in FONTS:
        return want
    best = "6x12"
    for name, (fw, fh, _) in sorted(FONTS.items(), key=lambda kv: kv[1][0]):
        if w // fw >= 38 and h // fh >= 12:
            best = name
    return best


class Screen:
    def __init__(self, window: tuple[int, int, int, int] | None = None,
                 fbdev: str = "/dev/fb0") -> None:
        self.mm = None
        self.fd = None
        self.tty = None
        headless = os.environ.get("HLINK_HEADLESS")
        if headless:
            fw, fh = (int(v) for v in headless.split("x"))
            self.fb_w, self.fb_h, self.bpp, self.line = fw, fh, 16, fw * 2
            self.offsets = (11, 5, 0)
        else:
            import fcntl
            import mmap
            self.fd = os.open(fbdev, os.O_RDWR)
            var = bytearray(160)
            fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, var, True)
            fix = FixInfo()
            fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, fix, True)
            self.fb_w, self.fb_h, _, _, _, _, self.bpp = struct.unpack_from("<7I", var, 0)
            self.offsets = (struct.unpack_from("<I", var, 32)[0], struct.unpack_from("<I", var, 44)[0],
                            struct.unpack_from("<I", var, 56)[0])
            self.line = fix.line_length or self.fb_w * self.bpp // 8
            # only the first buffer: FunKey's SPI panel driver flushes that one,
            # and mapping the other two of its 240x720 virtual screen can fault
            self.mm = mmap.mmap(self.fd, self.line * self.fb_h, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE)
            try:
                struct.pack_into("<II", var, 16, 0, 0)
                fcntl.ioctl(self.fd, FBIOPAN_DISPLAY, var, True)
            except OSError:
                pass
        if self.bpp not in (16, 32):
            raise RuntimeError("a %d-bit framebuffer is not supported" % self.bpp)
        self.Bpp = self.bpp // 8
        x, y, w, h = window or (0, 0, self.fb_w, self.fb_h)
        self.wx, self.wy = x, y
        self.w, self.h = min(w, self.fb_w - x), min(h, self.fb_h - y)
        self.stride = self.w * self.Bpp
        self.buf = bytearray(self.stride * self.h)
        self.font = pick_font(self.w, self.h)
        self.fw, self.fh, self.glyphs = FONTS[self.font]
        self.gbytes = (self.fw + 7) // 8
        self._colors: dict = {}
        self._cache: dict = {}

    # ----------------------------------------------------------- colors --
    def color(self, rgb: tuple[int, int, int]) -> bytes:
        c = self._colors.get(rgb)
        if c is None:
            r, g, b = rgb
            if self.bpp == 16:
                v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                c = struct.pack("<H", v)
            else:
                ro, go, bo = self.offsets
                c = struct.pack("<I", (r << ro) | (g << go) | (b << bo))
            self._colors[rgb] = c
        return c

    # ---------------------------------------------------------- drawing --
    def fill(self, rgb) -> None:
        self.buf[:] = self.color(rgb) * (self.w * self.h)

    def rect(self, x: int, y: int, w: int, h: int, rgb) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = self.color(rgb) * (x1 - x0)
        n = len(row)
        for yy in range(y0, y1):
            off = yy * self.stride + x0 * self.Bpp
            self.buf[off:off + n] = row

    def frame(self, x: int, y: int, w: int, h: int, rgb, t: int = 1) -> None:
        self.rect(x, y, w, t, rgb)
        self.rect(x, y + h - t, w, t, rgb)
        self.rect(x, y, t, h, rgb)
        self.rect(x + w - t, y, t, h, rgb)

    def _glyph(self, ch: str, fg, bg) -> list[bytes]:
        key = (ch, fg, bg)
        rows = self._cache.get(key)
        if rows is None:
            data = bytes.fromhex(self.glyphs.get(ch) or self.glyphs["?"])
            on, off = self.color(fg), self.color(bg)
            rows = []
            for r in range(self.fh):
                bits = int.from_bytes(data[r * self.gbytes:(r + 1) * self.gbytes], "big")
                top = 1 << (self.gbytes * 8 - 1)
                rows.append(b"".join(on if bits & (top >> c) else off for c in range(self.fw)))
            self._cache[key] = rows
        return rows

    def text(self, x: int, y: int, s: str, fg, bg, max_w: int | None = None) -> int:
        """Draw s on a solid background; returns the x after it."""
        limit = self.w if max_w is None else min(self.w, x + max_w)
        for ch in s:
            if x + self.fw > limit:
                break
            if x >= 0 and 0 <= y and y + self.fh <= self.h:
                off = y * self.stride + x * self.Bpp
                for row in self._glyph(ch, fg, bg):
                    self.buf[off:off + len(row)] = row
                    off += self.stride
            x += self.fw
        return x

    def cols(self, px: int) -> int:
        return max(0, px // self.fw)

    def fit(self, s: str, px: int) -> str:
        """s cut to px with an ellipsis."""
        n = self.cols(px)
        if len(s) <= n:
            return s
        return (s[:n - 1] + "…") if n > 1 else s[:n]

    def flush(self) -> None:
        if self.mm is None:
            return
        if self.wx == 0 and self.w == self.fb_w and self.line == self.stride:
            off = self.wy * self.line
            self.mm[off:off + len(self.buf)] = self.buf
            return
        for yy in range(self.h):
            off = (self.wy + yy) * self.line + self.wx * self.Bpp
            self.mm[off:off + self.stride] = self.buf[yy * self.stride:(yy + 1) * self.stride]

    def save_png(self, path: str) -> None:
        """The drawing area as a PNG (16-bit only, for previews)."""
        raw = bytearray()
        for yy in range(self.h):
            raw.append(0)
            for xx in range(self.w):
                v = struct.unpack_from("<H", self.buf, yy * self.stride + xx * 2)[0]
                raw += bytes(((v >> 8) & 0xF8, (v >> 3) & 0xFC, (v << 3) & 0xF8))

        def chunk(kind, data):
            c = kind + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)

    # --------------------------------------------------------- console --
    def take_console(self, tty: str = "/dev/tty0") -> None:
        """Keep the text console from drawing over the screen."""
        if self.mm is None:
            return
        import fcntl
        try:
            self.tty = os.open(tty, os.O_RDWR)
            mode = bytearray(4)
            fcntl.ioctl(self.tty, KDGETMODE, mode, True)
            self.old_mode = struct.unpack("<I", mode)[0]
            fcntl.ioctl(self.tty, KDSETMODE, KD_GRAPHICS)
        except OSError:
            self.tty = None

    def close(self) -> None:
        if self.tty is not None:
            import fcntl
            try:
                fcntl.ioctl(self.tty, KDSETMODE, self.old_mode)
            except OSError:
                pass
            os.close(self.tty)
            self.tty = None
        if self.mm is not None:
            self.mm.close()
            os.close(self.fd)
            self.mm = None


def fold(s: str, glyphs) -> str:
    """Letters the font lacks, as their base letter (or ?)."""
    out = []
    for ch in s:
        if ch in glyphs:
            out.append(ch)
            continue
        base = unicodedata.normalize("NFKD", ch)[:1]
        out.append(base if base in glyphs else "?")
    return "".join(out)


# ------------------------------------------------------------- buttons --
EV_KEY = 0x01
EV = struct.Struct("llHHi")
# FunKey OS: fkgpiod turns the buttons into these keys
FUNKEY_KEYS = {
    22: "up", 32: "down", 38: "left", 19: "right",
    30: "a", 48: "b", 45: "x", 21: "y",
    31: "start", 16: "menu", 37: "select", 50: "l", 49: "r",
}
REPEATS = {"up", "down", "left", "right"}


class Buttons:
    """Button presses from an evdev keyboard, with our own key repeat."""

    def __init__(self, dev: str = "/dev/input/event0", keys: dict | None = None) -> None:
        self.fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
        self.keys = keys or FUNKEY_KEYS
        self.queue: list[str] = []
        self.held: str | None = None
        self.next_repeat = 0.0
        self.drain()

    def drain(self) -> None:
        try:
            while os.read(self.fd, EV.size * 64):
                pass
        except BlockingIOError:
            pass
        self.queue.clear()
        self.held = None

    def _read(self) -> None:
        try:
            data = os.read(self.fd, EV.size * 64)
        except BlockingIOError:
            return
        for i in range(0, len(data) - EV.size + 1, EV.size):
            _, _, kind, code, value = EV.unpack_from(data, i)
            name = self.keys.get(code) if kind == EV_KEY else None
            if not name:
                continue
            if value == 1:
                self.queue.append(name)
                if name in REPEATS:
                    self.held = name
                    self.next_repeat = time.monotonic() + 0.35
            elif value == 0 and self.held == name:
                self.held = None

    def get(self, timeout: float | None) -> str | None:
        """The next press, or None after timeout seconds (0: don't wait)."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            self._read()
            if self.queue:
                return self.queue.pop(0)
            now = time.monotonic()
            if self.held and now >= self.next_repeat:
                self.next_repeat = now + 0.07
                return self.held
            wait = None if end is None else max(0.0, end - now)
            if self.held:
                wait = max(0.0, self.next_repeat - now) if wait is None else min(wait, max(0.0, self.next_repeat - now))
            if wait == 0.0 and end is not None and now >= end:
                return None
            select.select([self.fd], [], [], wait)

    def close(self) -> None:
        os.close(self.fd)
