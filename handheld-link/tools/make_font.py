#!/usr/bin/env python3
"""Writes hlink/font.py from Terminus console fonts (PSF2 with a Unicode table).

    python3 tools/make_font.py /usr/share/consolefonts > hlink/font.py

Only printable ASCII and Latin-1 are kept; the app folds other letters to
these (e with an accent to e) before drawing.
"""

import gzip
import os
import struct
import sys

FONTS = [("Lat15-Terminus12x6.psf.gz", "6x12"),
         ("Lat15-Terminus16.psf.gz", "8x16"),
         ("Lat15-Terminus24x12.psf.gz", "12x24")]
WANT = [chr(c) for c in range(0x20, 0x7F)] + [chr(c) for c in range(0xA1, 0x100)] + ["…"]


def load(path):
    data = gzip.open(path).read()
    chars = {}
    if data[:2] == b"\x36\x04":                       # PSF1: 8 pixels wide
        mode, csize = data[2], data[3]
        if not mode & 2:
            raise SystemExit("%s: no Unicode table" % path)
        count = 512 if mode & 1 else 256
        w, h, hsize = 8, csize, 4
        glyphs = [data[hsize + i * csize:hsize + (i + 1) * csize] for i in range(count)]
        table = data[hsize + count * csize:]
        codes = struct.unpack("<%dH" % (len(table) // 2), table[:len(table) // 2 * 2])
        i, seq = 0, False
        for c in codes:
            if c == 0xFFFF:
                i, seq = i + 1, False
            elif c == 0xFFFE:
                seq = True
            elif not seq and i < count:
                chars.setdefault(chr(c), i)
        return w, h, glyphs, chars
    magic, _, hsize, flags, count, csize, h, w = struct.unpack("<8I", data[:32])
    if magic != 0x864AB572 or not flags & 1:
        raise SystemExit("%s: not a PSF font with a Unicode table" % path)
    glyphs = [data[hsize + i * csize:hsize + (i + 1) * csize] for i in range(count)]
    table = data[hsize + count * csize:]
    for i, entry in enumerate(table.split(b"\xff")[:count]):
        for ch in entry.split(b"\xfe")[0].decode("utf-8", "replace"):
            chars.setdefault(ch, i)
    return w, h, glyphs, chars


def main():
    top = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/consolefonts"
    print('"""Bitmap fonts, made by tools/make_font.py from the Terminus Font,')
    print('Copyright (C) 2020 Dimitar Toshkov Zhekov, under the SIL Open Font')
    print('License 1.1 (see FONT-LICENSE.txt).')
    print("")
    print("FONTS[name] = (width, height, {char: hex rows}); each row is")
    print('ceil(width / 8) bytes, the leftmost pixel in the top bit."""')
    print("")
    print("FONTS = {")
    for fname, name in FONTS:
        w, h, glyphs, chars = load(os.path.join(top, fname))
        print("    %r: (%d, %d, {" % (name, w, h))
        for ch in WANT:
            if ch in chars:
                print("        %r: %r," % (ch, glyphs[chars[ch]].hex()))
        print("    }),")
    print("}")


if __name__ == "__main__":
    main()
