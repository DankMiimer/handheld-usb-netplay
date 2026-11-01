"""What a handheld has to share, and where received files go.

Both handhelds describe their files the same way: a category (ROMs, saves,
...), a group in it (a system such as "gba", or an apps folder), and items
in the group (a game, an app). An item is one or more files, named relative
to the item's folder. The receiving handheld decides where each file goes on
its own card, so a Game Boy Advance save lands in the GBA emulator's save
folder whatever the other handheld calls it.

FunKey is the only layout so far: DrUm78's FunKey OS on the RG Nano, the GBA
Mini, the FunKey S and other clones (ROM folders in /mnt, picoarch saves in
/mnt/FunKey/.picoarch/data/<core>).
"""

from __future__ import annotations

import os
import re
import shutil
import time

CATEGORIES = [
    ("roms", "ROMs"),
    ("saves", "Saves"),
    ("states", "Save states"),
    ("art", "Game art"),
    ("apps", "Apps"),
    ("settings", "Settings"),
]
CATEGORY_NAMES = dict(CATEGORIES)

# id, name, ROM folder in the card's root, picoarch core (its save folder),
# ROM extensions (from each emulator's menu entry)
SYSTEMS = [
    ("gb", "Game Boy", "Game Boy", "gambatte", "gb gbc dmg zip"),
    ("gbc", "Game Boy Color", "Game Boy Color", "gambatte", "gb gbc dmg zip"),
    ("gba", "Game Boy Advance", "Game Boy Advance", "gpsp", "gba bin agb gbz u1 zip"),
    ("nes", "NES", "NES", "fceumm", "fds nes unf unif zip"),
    ("snes", "Super NES", "SNES", "snes9x2005", "smc fig sfc gd3 gd7 dx2 bsx swc zip"),
    ("md", "Mega Drive", "Sega Genesis", "picodrive",
     "bin gen smd md 32x cue iso chd cso sms gg sg sc m3u 68k sgd pco zip"),
    ("sms", "Master System", "Sega Master System", "picodrive", "sms sg bin zip"),
    ("gg", "Game Gear", "Game Gear", "picodrive", "gg bin zip"),
    ("pce", "PC Engine", "PCE-TurboGrafx", "mednafen_pce_fast", "pce cue ccd chd toc m3u zip"),
    ("lynx", "Atari Lynx", "Atari lynx", "mednafen_lynx", "lnx lyx bll o zip"),
    ("ngp", "Neo Geo Pocket", "Neo Geo Pocket", "mednafen_ngp", "ngp ngc ngpc npc zip"),
    ("ws", "WonderSwan", "WonderSwan", "mednafen_wswan", "ws wsc pc2 zip"),
    ("ps1", "PlayStation", "PS1", "pcsx_rearmed", "bin cue img mdf pbp toc cbn m3u chd iso"),
    ("pico8", "PICO-8", "PICO-8", "fake08", "p8 png"),
    ("pokemini", "Pokemon Mini", "Pokemon Mini", "pokemini", "min zip"),
    ("fba", "FB Alpha 2012", "Final Burn Alpha 2012", "fbalpha2012", "zip"),
    ("mame", "MAME 2000", "MAME 2000", "mame2000", "zip"),
    ("nds", "Nintendo DS", "NDS", None, "nds zip 7z rar"),
    ("n64", "Nintendo 64", "Nintendo 64", None, "z64 n64 v64 zip"),
]

APP_FOLDERS = ["Applications", "Native games", "Emulators", "Libretro", "Settings"]

SAVE_EXTS = {"sav", "srm", "rtc", "flash", "eep", "fla", "ssv", "mcd", "mcr", "mpk", "ngf"}
STATE_RE = re.compile(r"^(.+?)\.(st\d+|quicksave|00\d)(\.bmp)?$", re.I)
GAME_CFG_EXTS = {"cfg", "fkcfg"}
DS_STATE_RE = re.compile(r"^(.+)_(\d+)\.dss$", re.I)

# /mnt/FunKey folders that are the menus' own, logs, or covered elsewhere
NOT_APP_DATA = {".picoarch", ".retrofe", ".gmenu2x", ".frontend", "snapshots", "hlink"}

PART = ".hlink-part"


class ContentError(Exception):
    """A request this handheld can't place or find (shown to the user)."""


def safe_rel(rel: str) -> list[str]:
    """Split a relative file name, refusing anything that could leave its folder."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")):
        raise ContentError("bad file name")
    parts = rel.replace("\\", "/").split("/")
    for p in parts:
        if p in ("", ".", "..") or "\0" in p or p.endswith(PART):
            raise ContentError("bad file name")
    return parts


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _files(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return [e for e in it if e.is_file() and not e.name.startswith(".")
                    and not e.name.endswith(PART)]
    except OSError:
        return []


def _info(path: str, rel: str) -> dict:
    st = os.stat(path)
    return {"rel": rel, "size": st.st_size, "mtime": int(st.st_mtime)}


def _referenced(path: str) -> list[str]:
    """The track files a .cue sheet or the discs an .m3u playlist names."""
    out = []
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return out
    if path.lower().endswith(".cue"):
        for m in re.finditer(r'^\s*FILE\s+"([^"]+)"', text, re.M | re.I):
            out.append(m.group(1))
    else:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return [r for r in out if "/" not in r and "\\" not in r and r not in (".", "..")]


class FunKeyContent:
    def __init__(self, root: str = "/mnt") -> None:
        self.root = root
        self.funkey = os.path.join(root, "FunKey")
        self.saves_root = os.path.join(self.funkey, ".picoarch", "data")
        self.systems = {s[0]: s for s in SYSTEMS}
        self._stems: dict[str, tuple[float, set[str]]] = {}

    # ------------------------------------------------------------ places --
    def _system(self, group: str):
        s = self.systems.get(group)
        if s is None:
            raise ContentError("no place for %s here" % group)
        return s

    def _rom_dir(self, group: str) -> str:
        return os.path.join(self.root, self._system(group)[2])

    def _save_dir(self, group: str) -> str:
        if group == "nds":
            return os.path.join(self.root, "drastic", "backup")
        if group.startswith("core:"):
            core = group[5:]
            safe_rel(core)
            return os.path.join(self.saves_root, core)
        core = self._system(group)[3]
        if core is None:
            raise ContentError("no save folder for %s here" % group)
        return os.path.join(self.saves_root, core)

    def _state_dir(self, group: str) -> str:
        if group == "nds":
            return os.path.join(self.root, "drastic", "savestates")
        return self._save_dir(group)

    def _base(self, cat: str, group: str) -> str:
        """The folder an item's files are relative to."""
        if cat in ("roms", "art"):
            return self._rom_dir(group)
        if cat == "saves":
            return self._save_dir(group)
        if cat == "states":
            return self._state_dir(group)
        if cat == "apps":
            if group not in APP_FOLDERS:
                raise ContentError("no apps folder %s here" % group)
            return os.path.join(self.root, group)
        if cat == "settings":
            if group == "appdata":
                return self.funkey
            if group == "game":
                return self.saves_root
            raise ContentError("no settings group %s here" % group)
        raise ContentError("unknown category %s" % cat)

    def path(self, cat: str, group: str, rel: str) -> str:
        """Where a file of this item is, or goes, on this handheld."""
        return os.path.join(self._base(cat, group), *safe_rel(rel))

    def free(self, cat: str, group: str) -> int:
        d = self._base(cat, group)
        while not os.path.isdir(d) and os.path.dirname(d) != d:
            d = os.path.dirname(d)
        return shutil.disk_usage(d).free

    # ------------------------------------------------------------ groups --
    def groups(self, cat: str) -> list[dict]:
        out = []
        if cat in ("roms", "art", "saves", "states"):
            for sid, name, *_ in SYSTEMS:
                n = len(self.items(cat, sid))
                if n:
                    out.append({"id": sid, "name": name, "count": n})
            if cat in ("saves", "states"):
                for core in self._other_cores():
                    n = len(self.items(cat, "core:" + core))
                    if n:
                        out.append({"id": "core:" + core, "name": core, "count": n})
        elif cat == "apps":
            for folder in APP_FOLDERS:
                n = len(self.items(cat, folder))
                if n:
                    out.append({"id": folder, "name": folder, "count": n})
        elif cat == "settings":
            for gid, name in (("appdata", "App data"), ("game", "Game settings")):
                n = len(self.items(cat, gid))
                if n:
                    out.append({"id": gid, "name": name, "count": n})
        else:
            raise ContentError("unknown category %s" % cat)
        return out

    def _other_cores(self) -> list[str]:
        known = {s[3] for s in SYSTEMS if s[3]}
        try:
            names = sorted(os.listdir(self.saves_root))
        except OSError:
            return []
        return [n for n in names if n not in known
                and os.path.isdir(os.path.join(self.saves_root, n))]

    # ------------------------------------------------------------- items --
    # Listings have names only: on a FAT card each file's size is a lookup
    # that scans its folder, which takes seconds in a folder of 2000 ROMs.
    # sizes() gives them for the few rows on the screen.
    def items(self, cat: str, group: str) -> list[dict]:
        """[{id, name}] sorted by name; id is what files() and sizes() take."""
        try:
            found = getattr(self, "_items_" + cat)(group)
        except ContentError:
            return []
        found.sort(key=lambda i: i["name"].lower())
        return found

    def sizes(self, cat: str, group: str, ids: list[str]) -> dict[str, int | None]:
        """{id: total size of its files}; None when that would mean a big walk."""
        out: dict[str, int | None] = {}
        for i in ids:
            if cat == "settings" and group == "appdata":
                out[i] = None
                continue
            try:
                out[i] = sum(f["size"] for f in self.files(cat, group, i))
            except (ContentError, OSError):
                out[i] = None
        return out

    def _roms(self, group: str) -> tuple[list[str], set[str]]:
        """A system's ROM files, and the files a .cue/.m3u among them uses."""
        sid = self._system(group)
        exts = set(sid[4].split())
        d = self._rom_dir(group)
        names = [e.name for e in _files(d) if _ext(e.name) in exts]
        used = set()
        for name in names:
            if _ext(name) in ("cue", "m3u"):
                used.update(_referenced(os.path.join(d, name)))
        return names, used

    def _items_roms(self, group: str) -> list[dict]:
        names, used = self._roms(group)
        return [{"id": n, "name": n} for n in names if n not in used]

    def _items_art(self, group: str) -> list[dict]:
        sid = self._system(group)
        if "png" in sid[4].split():     # PICO-8 carts are the pictures
            return []
        return [{"id": e.name, "name": _stem(e.name)}
                for e in _files(self._rom_dir(group)) if _ext(e.name) == "png"]

    def _rom_stems(self, group: str) -> set[str]:
        # asked once per save of a shared emulator: keep it for a few seconds
        hit = self._stems.get(group)
        if hit and time.monotonic() - hit[0] < 10:
            return hit[1]
        try:
            names, _ = self._roms(group)
        except ContentError:
            names = []
        stems = {_stem(n) for n in names}
        self._stems[group] = (time.monotonic(), stems)
        return stems

    def _owner(self, group: str, stem: str) -> bool:
        """Systems sharing an emulator share its save folder: a save belongs to
        the system that has its game, else to the first system of the emulator."""
        if group.startswith("core:") or group == "nds":
            return True
        core = self._system(group)[3]
        sharing = [s[0] for s in SYSTEMS if s[3] == core]
        if len(sharing) == 1:
            return True
        for sid in sharing:
            if stem in self._rom_stems(sid):
                return sid == group
        return sharing[0] == group

    def _grouped(self, d: str, key) -> dict[str, list[os.DirEntry]]:
        out: dict[str, list[os.DirEntry]] = {}
        for e in _files(d):
            stem = key(e.name)
            if stem:
                out.setdefault(stem, []).append(e)
        return out

    def _items_by_stem(self, group: str, d: str, key) -> list[dict]:
        out = []
        for stem in self._grouped(d, key):
            if self._owner(group, stem):
                out.append({"id": stem, "name": stem})
        return out

    def _save_key(self, group: str):
        if group == "nds":
            return lambda n: _stem(n) if _ext(n) == "dsv" else None
        return lambda n: _stem(n) if _ext(n) in SAVE_EXTS else None

    def _state_key(self, group: str):
        if group == "nds":
            def key(n):
                m = DS_STATE_RE.match(n)
                return m.group(1) if m and not n.startswith("_") else None
            return key

        def key(n):
            m = STATE_RE.match(n)
            return m.group(1) if m else None
        return key

    def _items_saves(self, group: str) -> list[dict]:
        return self._items_by_stem(group, self._save_dir(group), self._save_key(group))

    def _items_states(self, group: str) -> list[dict]:
        return self._items_by_stem(group, self._state_dir(group), self._state_key(group))

    def _items_apps(self, group: str) -> list[dict]:
        d = self._base("apps", group)
        out = [{"id": e.name, "name": _stem(e.name)} for e in _files(d) if _ext(e.name) == "opk"]
        if group == "Libretro":
            out += [{"id": "cores/" + e.name, "name": "core: " + _stem(e.name)}
                    for e in _files(os.path.join(d, "cores")) if _ext(e.name) == "so"]
        return out

    def _items_settings(self, group: str) -> list[dict]:
        out = []
        if group == "appdata":
            try:
                entries = list(os.scandir(self.funkey))
            except OSError:
                return []
            for e in entries:
                if e.is_dir() and e.name not in NOT_APP_DATA:
                    name = e.name[1:] if e.name.startswith(".") else e.name
                    out.append({"id": e.name, "name": name})
        elif group == "game":
            try:
                cores = sorted(os.listdir(self.saves_root))
            except OSError:
                return []
            for core in cores:
                for e in _files(os.path.join(self.saves_root, core)):
                    if _ext(e.name) in GAME_CFG_EXTS:
                        what = "all games" if e.name == "picoarch.cfg" else _stem(e.name)
                        out.append({"id": core + "/" + e.name, "name": "%s (%s)" % (what, core)})
        else:
            raise ContentError("no settings group %s here" % group)
        return out

    # ------------------------------------------------------------- files --
    def files(self, cat: str, group: str, item: str) -> list[dict]:
        """The files of one item, as [{rel, size, mtime}], for sending it."""
        base = self._base(cat, group)
        if cat == "roms":
            safe_rel(item)
            rels = [item]
            if _ext(item) in ("cue", "m3u"):
                rels += _referenced(os.path.join(base, item))
        elif cat in ("art", "apps"):
            safe_rel(item)
            rels = [item]
            if cat == "apps" and _ext(item) == "opk":
                rels.append(_stem(item) + ".png")
        elif cat in ("saves", "states"):
            key = self._save_key(group) if cat == "saves" else self._state_key(group)
            rels = sorted(e.name for e in self._grouped(base, key).get(item, []))
        elif cat == "settings" and group == "appdata":
            safe_rel(item)
            rels = [item + "/" + r for r in _tree(os.path.join(base, item))]
        elif cat == "settings":
            safe_rel(item)
            rels = [item]
        else:
            raise ContentError("unknown category %s" % cat)
        out = []
        for rel in rels:
            p = self.path(cat, group, rel)
            if os.path.isfile(p):
                out.append(_info(p, rel))
        if not out:
            raise ContentError("%s is gone" % item)
        return out

    def have(self, cat: str, group: str, rels: list[str]) -> dict[str, dict]:
        """Which of these files are already on this handheld."""
        out = {}
        for rel in rels:
            try:
                p = self.path(cat, group, rel)
            except ContentError:
                continue
            if os.path.isfile(p):
                out[rel] = _info(p, rel)
        return out


def _tree(top: str) -> list[str]:
    out = []
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for f in sorted(files):
            if not f.endswith(PART):
                out.append(os.path.relpath(os.path.join(d, f), top).replace(os.sep, "/"))
    return out
