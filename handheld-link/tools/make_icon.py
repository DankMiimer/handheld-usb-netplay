#!/usr/bin/env python3
"""Draws Handheld Link's 32x32 menu icon (no PIL needed).

    python3 tools/make_icon.py out.png [--preview]    (--preview: also out_x8.png)
"""

import struct
import sys
import zlib

W = H = 32
CLEAR = (0, 0, 0, 0)
BG = (30, 40, 64, 255)
RIM = (86, 168, 255, 255)
BODY = (220, 226, 236, 255)
SCREEN = (38, 72, 124, 255)
ARROW = (255, 196, 64, 255)


def draw():
    px = [[CLEAR] * W for _ in range(H)]

    def rect(x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if 0 <= x < W and 0 <= y < H:
                    px[y][x] = c

    for y in range(H):              # rounded tile
        for x in range(W):
            corner = min(x, W - 1 - x) + min(y, H - 1 - y)
            if corner >= 2:
                edge = x in (0, W - 1) or y in (0, H - 1) or corner < 3
                px[y][x] = RIM if edge else BG
    for x0 in (4, 19):              # two handhelds
        rect(x0, 7, x0 + 8, 24, BODY)
        rect(x0 + 1, 8, x0 + 7, 14, SCREEN)
        rect(x0 + 2, 18, x0 + 2, 18, SCREEN)
        rect(x0 + 1, 19, x0 + 3, 19, SCREEN)
        rect(x0 + 2, 20, x0 + 2, 20, SCREEN)
        rect(x0 + 6, 19, x0 + 6, 19, SCREEN)
    rect(13, 10, 18, 10, ARROW)     # an arrow each way between them
    rect(17, 9, 17, 11, ARROW)
    rect(13, 15, 18, 15, ARROW)
    rect(14, 14, 14, 16, ARROW)
    return px


def png(px, scale=1):
    raw = bytearray()
    for row in px:
        line = bytearray()
        for c in row:
            line += bytes(c) * scale
        for _ in range(scale):
            raw.append(0)
            raw += line

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = sys.argv[1]
    px = draw()
    with open(out, "wb") as f:
        f.write(png(px))
    if "--preview" in sys.argv:
        with open(out.rsplit(".", 1)[0] + "_x8.png", "wb") as f:
            f.write(png(px, 8))
