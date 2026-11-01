#!/usr/bin/env python3
"""Draws Handheld Link's screens into PNGs, with no handheld: for checking
the layout at other screen sizes.

    python3 tools/preview.py OUTDIR [WxH ...]      (default 240x240 240x160 640x480)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

CARD = {
    "Game Boy Advance/Pokemon - Emerald Version (USA, Europe).gba": 16 << 20,
    "Game Boy Advance/Advance Wars (Europe) (En,Fr,De,Es).gba": 8 << 20,
    "Game Boy Advance/Golden Sun (USA, Europe).gba": 8 << 20,
    "Game Boy Advance/Mario Kart - Super Circuit (USA).gba": 4 << 20,
    "Game Boy Advance/Metroid Fusion (USA).gba": 8 << 20,
    "Game Boy Advance/Pokémon Mystery Dungeon - Red Rescue Team (USA).gba": 32 << 20,
    "Game Boy Advance/Wario Land 4 (USA, Europe).gba": 8 << 20,
    "Game Boy Advance/Zelda - The Minish Cap (USA).gba": 16 << 20,
    "Game Boy Advance/Castlevania - Aria of Sorrow (USA).gba": 8 << 20,
    "Game Boy Advance/F-Zero - Maximum Velocity (USA, Europe).gba": 4 << 20,
    "Game Boy Advance/Kirby & The Amazing Mirror (USA).gba": 16 << 20,
    "Game Boy Advance/Sonic Advance (USA).gba": 8 << 20,
    "Game Boy Advance/Super Mario Advance 4 (USA).gba": 8 << 20,
    "Game Boy Advance/Tactics Ogre (USA).gba": 8 << 20,
    "Game Boy/Tetris (World).gb": 32 << 10,
    "SNES/Super Metroid (USA).sfc": 3 << 20,
    "FunKey/.picoarch/data/gpsp/Golden Sun (USA, Europe).sav": 64 << 10,
}


class FakeState:
    status = "linked"
    text = "Linked with GBA Mini"
    peer = None
    peer_info = {"name": "GBA Mini"}
    activity = ""
    activity_at = 0.0
    version = 0


class FakeManager:
    def __init__(self, peer):
        self.state = FakeState()
        self.state.peer = peer
        self.port = None

    def look_again(self):
        pass


class Script:
    """Buttons from a list; a snapshot is taken before each press."""

    def __init__(self, presses, snap):
        self.presses = list(presses)
        self.snap = snap

    def get(self, timeout):
        if not self.presses:
            if timeout == 0:
                return None
            raise StopIteration
        p = self.presses.pop(0)
        if p.startswith("@"):
            self.snap(p[1:])
            return self.get(timeout)
        return p


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "preview"
    sizes = sys.argv[2:] or ["240x240", "240x160", "640x480"]
    os.makedirs(out, exist_ok=True)
    tmp = tempfile.mkdtemp()
    for rel, size in CARD.items():
        p = os.path.join(tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.truncate(size)
    for size in sizes:
        os.environ["HLINK_HEADLESS"] = size
        from hlink.screen import Screen
        from hlink.content import FunKeyContent
        from hlink.service import LocalSide, CopyUI
        from hlink import app
        s = Screen()
        # a short screen is the GBA Mini's, browsing the RG Nano
        me, other = ("GBA Mini", "RG Nano") if s.h < 200 else ("RG Nano", "GBA Mini")
        local = LocalSide(FunKeyContent(tmp), me)
        peer = LocalSide(FunKeyContent(tmp), other)
        m = FakeManager(peer)
        m.state.peer_info = {"name": other}
        m.state.text = "Linked with " + other

        def snap(name, s=s, size=size):
            s.save_png(os.path.join(out, "%s-%s.png" % (size, name)))

        b = Script([], snap)
        ui = app.Ui(s, b, local, m, me)
        ui.draw(); snap("1-home")
        ui.handle("a"); ui.draw(); snap("2-systems")
        ui.handle("down"); ui.handle("a"); ui.handle("a"); ui.handle("down"); ui.handle("a")
        ui.handle("up"); ui.handle("up")
        ui.draw(); snap("3-games")
        ui.handle("r"); ui.draw(); snap("4-other-side")
        ui.handle("l")
        b.presses = ["@5-actions", "b"]
        try:
            ui.actions()
        except StopIteration:
            pass
        cs = app.CopyScreen(ui, other)
        cs.start -= 3
        cs.progress("Pokemon Mystery Dungeon - Red Rescue Team (USA)", 1, 3, 20 << 20, 56 << 20)
        snap("6-progress")
        b.presses = ["@7-already-there", "b"]
        cs.ask("Golden Sun (USA, Europe)", {"x": {"size": 8 << 20, "mtime": 1700000000}},
               [{"size": 8 << 20}])
        m.state.status, m.state.text, m.state.peer = "waiting", \
            "Open Handheld Link on the other handheld", None
        ui.handle("r"); ui.draw(); snap("8-not-linked")
        print("drew", size, "with font", s.font)


if __name__ == "__main__":
    main()
