"""Handheld Link's screens: browse both handhelds, mark items, send them.

    L / R      this handheld / the other one (same place in the lists)
    A          open; in a list of games or files, mark or unmark
    B          back
    Start      what to do with the marked items (send, mark all, ...)
"""

from __future__ import annotations

import time

from .content import CATEGORIES, ContentError
from .link import LINKED, PROBLEM
from .screen import fold
from .service import (REPLACE, REPLACE_ALL, SKIP, SKIP_ALL, STOP, CopyUI, PeerError,
                      copy_items, human)

BG = (14, 16, 22)
PANEL = (30, 35, 46)
TAB = (24, 28, 38)
TEXT = (232, 234, 240)
DIM = (138, 145, 160)
ACCENT = (86, 168, 255)
SEL = (38, 72, 124)
MARK = (255, 196, 64)
GOOD = (96, 204, 128)
BAD = (240, 96, 84)

NOTE_FOR = 4          # seconds "Receiving ..." stays on the bottom line
PROGRESS_EVERY = 0.5  # seconds between redraws of the copy screen


class Back(Exception):
    pass


class Ui:
    def __init__(self, screen, buttons, local, manager, me: str, log=lambda s: None) -> None:
        self.s = screen
        self.b = buttons
        self.local = local
        self.m = manager
        self.me = me
        self.log = log
        # start on the other handheld's page: until linked, it says what to do
        self.side = 1
        self.cat: str | None = None
        self.group: dict | None = None
        self.cursor: dict = {}
        self.top: dict = {}
        self.marks: dict = {}
        self.lists: dict = {}
        self.sel_since = time.monotonic()
        self.seen = -1
        self.mq: dict | None = None     # the selected row, while its long name can scroll
        self.hint = ""
        self.footer_at = 0.0
        self.note_until = 0.0
        fh = screen.fh
        self.row_h = fh + 2
        self.tabs_h = fh + 4
        self.crumb_h = fh + 2
        self.foot_h = fh + 3
        self.list_y = self.tabs_h + self.crumb_h
        self.rows = max(3, (screen.h - self.list_y - self.foot_h) // self.row_h)
        self.pad = max(2, screen.fw // 2)

    # ------------------------------------------------------------ state --
    def peer(self):
        st = self.m.state
        return st.peer if st.status == LINKED else None

    def peer_name(self) -> str:
        st = self.m.state
        return (st.peer_info.get("name") if st.status == LINKED else None) or "Other handheld"

    def side_obj(self, side: int):
        return self.local if side == 0 else self.peer()

    def key(self, side=None):
        return (self.side if side is None else side, self.cat,
                self.group["id"] if self.group else None)

    def level(self) -> int:
        return 0 if self.cat is None else (1 if self.group is None else 2)

    def rows_for(self, side: int):
        """The current list on a side: (rows, problem text)."""
        obj = self.side_obj(side)
        if obj is None:
            # not linked: the other side's page says why, and what to do
            return [], self.m.state.text
        if self.cat is None:
            return [{"id": c, "name": n, "right": ""} for c, n in CATEGORIES], None
        k = self.key(side)
        if k not in self.lists:
            self.busy("Loading")
            try:
                if self.group is None:
                    rows = [{"id": g["id"], "name": g["name"], "right": str(g["count"])}
                            for g in obj.groups(self.cat)]
                else:
                    rows = [{"id": i["id"], "name": i["name"]}
                            for i in obj.items(self.cat, self.group["id"])]
            except ConnectionError:
                return [], "The link went down"
            except (PeerError, ContentError, OSError) as e:
                return [], str(e)
            self.lists[k] = rows
        return self.lists[k], None

    def fill_sizes(self, rows: list[dict], first: int, last: int) -> None:
        """Sizes for the rows on the screen (listings come without them)."""
        want = [r["id"] for r in rows[first:last] if "size" not in r]
        obj = self.side_obj(self.side)
        if not want or obj is None or self.group is None:
            return
        try:
            got = obj.sizes(self.cat, self.group["id"], want)
        except ConnectionError:
            return
        except (PeerError, ContentError, OSError):
            got = {}
        for r in rows[first:last]:
            if "size" not in r:
                r["size"] = got.get(r["id"])
                r["right"] = human(r["size"]) if r["size"] is not None else ""

    def forget(self, cat=None, side=None) -> None:
        for k in list(self.lists):
            if (cat is None or k[1] == cat) and (side is None or k[0] == side):
                del self.lists[k]

    # ---------------------------------------------------------- drawing --
    def busy(self, text: str) -> None:
        s = self.s
        y = self.list_y
        s.rect(0, y, s.w, s.h - y - self.foot_h, BG)
        s.text(self.pad, y + self.row_h, text + "…", DIM, BG)
        s.flush()

    def draw_tabs(self) -> None:
        s = self.s
        half = s.w // 2
        names = [self.me, self.peer_name()]
        linked = self.peer() is not None
        for i, name in enumerate(names):
            x = i * half
            w = half if i == 0 else s.w - half
            active = i == self.side
            bg = SEL if active else TAB
            s.rect(x, 0, w, self.tabs_h, bg)
            color = TEXT if (i == 0 or linked) else DIM
            dot = s.fh // 3 if i == 1 else 0
            label = s.fit(fold(name, s.glyphs), w - 2 * self.pad - 2 * dot)
            tx = x + (w - len(label) * s.fw + 2 * dot) // 2
            if dot:
                # the other handheld's tab: a green dot while linked
                s.rect(tx - 2 * dot, (self.tabs_h - dot) // 2, dot, dot, GOOD if linked else DIM)
            s.text(tx, 2, label, color, bg)
        s.rect(0, self.tabs_h - 1, s.w, 1, ACCENT if self.side == 0 else (ACCENT if linked else DIM))

    def crumb(self) -> str:
        parts = []
        if self.cat:
            parts.append(dict(CATEGORIES)[self.cat])
        if self.group:
            parts.append(self.group["name"])
        return " > ".join(parts) if parts else "What to share"

    def draw_footer(self, hint: str) -> None:
        s = self.s
        y = s.h - self.foot_h
        s.rect(0, y, s.w, self.foot_h, PANEL)
        st = self.m.state
        self.footer_at = time.monotonic()
        if st.activity and self.footer_at - st.activity_at < NOTE_FOR:
            s.text(self.pad, y + 2, s.fit(fold(st.activity, s.glyphs), s.w - 2 * self.pad), ACCENT, PANEL)
            self.note_until = st.activity_at + NOTE_FOR
        else:
            s.text(self.pad, y + 2, s.fit(hint, s.w - 2 * self.pad), DIM, PANEL)
            self.note_until = 0.0

    def redraw_footer(self) -> None:
        """Only the bottom line (what is arriving), not the whole screen."""
        self.draw_footer(self.hint)
        self.s.flush()

    def step_marquee(self) -> None:
        """Scroll the selected row's long name once, redrawing only that row."""
        mq, s = self.mq, self.s
        t = time.monotonic() - self.sel_since
        if not mq or t < 1.0:
            return
        over = len(mq["name"]) - mq["cols"]
        step = int((t - 1.0) / 0.15)
        if step > over + 8:
            # one pass and a pause at the end: back to the start, and stop
            shown = s.fit(mq["name"], mq["avail"])
            self.mq = None
        else:
            shown = mq["name"][min(step, over):][:mq["cols"]]
        if shown != mq["last"]:
            s.rect(mq["x"], mq["ry"], mq["avail"], self.row_h, mq["bg"])
            s.text(mq["x"], mq["ry"] + 1, shown, TEXT, mq["bg"])
            s.flush()
            mq["last"] = shown

    def wrap(self, text: str, px: int) -> list[str]:
        n = max(1, self.s.cols(px))
        out = []
        for para in text.split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                while len(w) > n:
                    if line:
                        out.append(line)
                        line = ""
                    out.append(w[:n])
                    w = w[n:]
                if not line:
                    line = w
                elif len(line) + 1 + len(w) <= n:
                    line += " " + w
                else:
                    out.append(line)
                    line = w
            out.append(line)
        return out

    def draw(self) -> None:
        """Draw the whole browser (a long selected name then scrolls by itself
        through step_marquee)."""
        s = self.s
        self.mq = None
        s.fill(BG)
        self.draw_tabs()
        rows, problem = self.rows_for(self.side)
        k = self.key()
        n = len(rows)
        cur = min(self.cursor.get(k, 0), max(0, n - 1))
        self.cursor[k] = cur
        top = self.top.get(k, 0)
        if cur < top:
            top = cur
        if cur >= top + self.rows:
            top = cur - self.rows + 1
        top = max(0, min(top, max(0, n - self.rows)))
        self.top[k] = top
        if self.level() == 2:
            self.fill_sizes(rows, top, top + self.rows)
        # where we are, and how many
        y = self.tabs_h
        marks = self.marks.get(k, set())
        right = ""
        if self.level() == 2 and rows:
            right = ("%d marked" % len(marks)) if marks else str(n)
        cw = s.w - 2 * self.pad - (len(right) + 1) * s.fw
        s.text(self.pad, y + 1, s.fit(fold(self.crumb(), s.glyphs), cw), ACCENT, BG)
        if right:
            s.text(s.w - self.pad - len(right) * s.fw, y + 1, right, MARK if marks else DIM, BG)
        y = self.list_y
        if problem or not rows:
            text = problem or ("Nothing here" if self.level() else "")
            for i, line in enumerate(self.wrap(fold(text, s.glyphs), s.w - 2 * self.pad)[:self.rows]):
                s.text(self.pad, y + i * self.row_h + 1, line, BAD if self.m.state.status == PROBLEM else DIM, BG)
        for i in range(top, min(n, top + self.rows)):
            row = rows[i]
            ry = y + (i - top) * self.row_h
            selected = i == cur
            bg = SEL if selected else BG
            if selected:
                s.rect(0, ry, s.w, self.row_h, bg)
            x = self.pad
            if self.level() == 2:
                box = s.fh - 4
                bx, by = x, ry + (self.row_h - box) // 2
                if row["id"] in marks:
                    s.rect(bx, by, box, box, MARK)
                else:
                    s.frame(bx, by, box, box, DIM)
                x += box + s.fw // 2 + 1
            rt = row.get("right") or ""
            rw = (len(rt) * s.fw + self.pad) if rt else 0
            avail = s.w - x - self.pad - rw
            name = fold(row["name"], s.glyphs)
            cols = s.cols(avail)
            shown = s.fit(name, avail)
            if selected and len(name) > cols:
                self.mq = {"x": x, "ry": ry, "avail": avail, "bg": bg, "name": name,
                           "cols": cols, "last": shown}
            s.text(x, ry + 1, shown, TEXT, bg)
            if rt:
                s.text(s.w - self.pad - len(rt) * s.fw, ry + 1, rt, DIM if not selected else TEXT, bg)
        if n > self.rows:
            bar_h = s.h - self.list_y - self.foot_h
            th = max(4, bar_h * self.rows // n)
            ty = self.list_y + (bar_h - th) * top // max(1, n - self.rows)
            s.rect(s.w - 2, ty, 2, th, DIM)
        if self.level() == 2:
            hint = "A mark  START send  B back"
        elif self.level() == 1:
            hint = "A open  B back  L/R other side"
        else:
            hint = "A open  L/R other side  B quit"
        if self.side == 1 and self.peer() is None:
            hint = "A make the port a host  L back" if getattr(self.m, "port", None) else "L back"
        self.hint = hint
        self.draw_footer(hint)
        s.flush(full=True)

    # ---------------------------------------------------------- dialogs --
    def choose(self, title: str, lines: list[str], options: list[str], cur: int = 0) -> int | None:
        """A box with text and options; the chosen index, or None for B."""
        s = self.s
        while True:
            s.fill(BG)
            s.rect(0, 0, s.w, self.tabs_h, PANEL)
            s.text(self.pad, 2, s.fit(fold(title, s.glyphs), s.w - 2 * self.pad), ACCENT, PANEL)
            y = self.tabs_h + 2
            body = []
            for line in lines:
                body += self.wrap(fold(line, s.glyphs), s.w - 2 * self.pad)
            room = (s.h - y - self.foot_h) // self.row_h - len(options)
            for line in body[:max(0, room)]:
                s.text(self.pad, y, line, TEXT, BG)
                y += s.fh + 1
            y = s.h - self.foot_h - len(options) * self.row_h
            for i, opt in enumerate(options):
                bg = SEL if i == cur else BG
                s.rect(0, y, s.w, self.row_h, bg)
                s.text(self.pad, y + 1, s.fit(opt, s.w - 2 * self.pad), TEXT, bg)
                y += self.row_h
            self.draw_footer("A choose  B back")
            s.flush()
            btn = self.b.get(None)
            if btn == "up":
                cur = (cur - 1) % len(options)
            elif btn == "down":
                cur = (cur + 1) % len(options)
            elif btn in ("a", "start"):
                return cur
            elif btn in ("b", "menu"):
                return None

    def tell(self, title: str, lines: list[str]) -> None:
        self.choose(title, lines, ["OK"])

    # ------------------------------------------------------------- copy --
    def copy(self, items: list[dict]) -> None:
        peer = self.peer()
        if peer is None:
            self.tell("Not linked", [self.m.state.text])
            return
        src, dst = (self.local, peer) if self.side == 0 else (peer, self.local)
        cat, gid = self.cat, self.group["id"]
        ui = CopyScreen(self, dst.name)
        try:
            result = copy_items(src, dst, cat, gid, items, ui)
        except ConnectionError as e:
            self.log("copy: link down: %s" % e)
            self.forget()
            self.tell("The link went down", ["Copied so far stays copied. %s" % e])
            return
        took = time.monotonic() - ui.start
        self.log("copy %s/%s to %s: %s, %s/s" % (cat, gid, dst.name, result,
                                                 human(result["bytes"] / max(0.1, took))))
        self.forget(cat)
        k = self.key()
        if not result["failed"] and not result["stopped"]:
            self.marks.pop(k, None)
        lines = []
        if result["copied"]:
            lines.append("Copied %d to %s." % (result["copied"], dst.name))
        if result["skipped"]:
            lines.append("Skipped %d already there." % result["skipped"])
        if result["stopped"]:
            lines.append("Stopped.")
        for name, why in result["failed"][:4]:
            lines.append("Failed: %s: %s" % (name, why))
        if len(result["failed"]) > 4:
            lines.append("and %d more failed" % (len(result["failed"]) - 4))
        if not ui.told:
            self.tell("Done" if not result["failed"] else "Done, with problems", lines or ["Nothing copied."])

    def actions(self) -> None:
        rows, _ = self.rows_for(self.side)
        if not rows:
            return
        k = self.key()
        marks = self.marks.get(k, set())
        cur = rows[self.cursor.get(k, 0)]
        to = self.peer_name() if self.side == 0 else "this " + self.me
        verb = "Send" if self.side == 0 else "Copy"
        opts, acts = [], []
        if marks:
            picked = [r for r in rows if r["id"] in marks]
            sizes = [r.get("size") for r in picked]
            known = "" if None in sizes else " (%s)" % human(sum(sizes))
            opts.append("%s %d marked to %s%s" % (verb, len(picked), to, known))
            acts.append(("copy", picked))
        opts.append("%s this one to %s" % (verb, to))
        acts.append(("copy", [cur]))
        opts.append("Mark all %d" % len(rows))
        acts.append(("all", None))
        if marks:
            opts.append("Unmark all")
            acts.append(("none", None))
        i = self.choose(fold(cur["name"], self.s.glyphs) if not marks else "%d marked" % len(marks),
                        [], opts)
        if i is None:
            return
        what, arg = acts[i]
        if what == "copy":
            self.copy([{"id": r["id"], "name": r["name"]} for r in arg])
        elif what == "all":
            self.marks[k] = {r["id"] for r in rows}
        elif what == "none":
            self.marks.pop(k, None)

    # ------------------------------------------------------------- loop --
    def run(self) -> None:
        """Redraw only what changed: the whole screen after a button or a link
        change, the bottom line for files arriving (at most once a second),
        the selected row while its long name scrolls. A full redraw costs
        about 60 ms of CPU on an RG Nano, and each one goes out to the panel."""
        link = None
        received = 0.0
        full = True
        while True:
            st = self.m.state
            now = time.monotonic()
            if (st.status, st.text) != link:
                # linked, unlinked or relinked: the other side's lists are stale
                link = (st.status, st.text)
                self.forget(side=1)
                full = True
            if st.activity.startswith("Received") and st.activity_at > received \
                    and now - st.activity_at > 2:
                # files came in: this side's lists are stale once they stop coming
                received = st.activity_at
                self.forget(side=0)
                full = True
            if full:
                self.draw()
                self.seen = st.version
                full = False
            wait = 0.15 if self.mq else 1.0
            if st.activity and now - st.activity_at < NOTE_FOR:
                wait = min(wait, max(0.05, st.activity_at + NOTE_FOR - now))
            btn = self.b.get(wait)
            if btn is not None:
                if self.handle(btn) is False:
                    return
                full = True
                continue
            st = self.m.state
            now = time.monotonic()
            if (st.status, st.text) != link:
                continue
            if st.version != self.seen and now - self.footer_at >= 1.0:
                self.seen = st.version
                self.redraw_footer()
            elif self.note_until and now >= self.note_until:
                self.redraw_footer()
            elif self.mq:
                self.step_marquee()

    def move(self, delta: int, wrap: bool) -> None:
        rows, _ = self.rows_for(self.side)
        if not rows:
            return
        k = self.key()
        n = len(rows)
        cur = self.cursor.get(k, 0) + delta
        if wrap:
            cur %= n
        else:
            cur = max(0, min(n - 1, cur))
        self.cursor[k] = cur
        self.sel_since = time.monotonic()

    def handle(self, btn: str):
        lvl = self.level()
        if btn == "up":
            self.move(-1, True)
        elif btn == "down":
            self.move(1, True)
        elif btn == "left":
            self.move(-self.rows, False)
        elif btn == "right":
            self.move(self.rows, False)
        elif btn in ("l", "r"):
            self.side = 0 if btn == "l" else 1
            self.sel_since = time.monotonic()
            if self.side == 1:
                self.forget(side=1)
        elif btn == "a":
            if self.side == 1 and self.peer() is None:
                self.m.look_again()
                return
            rows, _ = self.rows_for(self.side)
            if not rows:
                return
            k = self.key()
            row = rows[min(self.cursor.get(k, 0), len(rows) - 1)]
            if lvl == 0:
                self.cat = row["id"]
            elif lvl == 1:
                self.group = {"id": row["id"], "name": row["name"]}
            else:
                marks = self.marks.setdefault(k, set())
                marks.symmetric_difference_update({row["id"]})
                self.move(1, False)
            self.sel_since = time.monotonic()
        elif btn in ("b", "menu"):
            if lvl == 2:
                self.group = None
            elif lvl == 1:
                self.cat = None
            elif self.choose("Quit Handheld Link?", [], ["Quit", "Stay"]) == 0:
                return False
        elif btn in ("start", "x") and lvl == 2:
            self.actions()


class CopyScreen(CopyUI):
    def __init__(self, ui: Ui, dst_name: str) -> None:
        self.ui = ui
        self.dst = dst_name
        self.start = time.monotonic()
        self.last = 0.0
        self.told = False

    def ask(self, name, theirs, ours):
        def when(files):
            return time.strftime("%Y-%m-%d", time.gmtime(max((f.get("mtime", 0) for f in files), default=0)))
        there = sum(f["size"] for f in theirs.values())
        here = sum(f["size"] for f in ours)
        opts = ["Replace", "Skip", "Replace all", "Skip all", "Stop"]
        lines = [name,
                 "There: %s, %s" % (human(there), when(theirs.values())),
                 "New:   %s, %s" % (human(here), when(ours))]
        i = self.ui.choose("Already on %s" % self.dst, lines, opts)
        self.last = 0.0
        return [REPLACE, SKIP, REPLACE_ALL, SKIP_ALL, STOP][i] if i is not None else STOP

    def problem(self, text):
        self.told = True
        self.ui.tell("Can't copy", [text])

    def progress(self, name, index, count, done, total):
        now = time.monotonic()
        if now - self.last < PROGRESS_EVERY and done < total:
            return True
        self.last = now
        ui, s = self.ui, self.ui.s
        if ui.b.get(0) in ("b", "menu"):
            if ui.choose("Stop copying?", ["The file being copied now is dropped; the ones done stay."],
                         ["Keep copying", "Stop"]) == 1:
                return False
            self.last = 0.0
        s.fill(BG)
        s.rect(0, 0, s.w, ui.tabs_h, PANEL)
        s.text(ui.pad, 2, s.fit(fold("Copying to " + self.dst, s.glyphs), s.w - 2 * ui.pad), ACCENT, PANEL)
        y = ui.tabs_h + s.fh // 2
        for line in ui.wrap(fold(name, s.glyphs), s.w - 2 * ui.pad)[:3]:
            s.text(ui.pad, y, line, TEXT, BG)
            y += s.fh + 1
        y += s.fh // 2
        s.text(ui.pad, y, "%d of %d" % (index + 1, count), DIM, BG)
        y += s.fh + 4
        bw = s.w - 2 * ui.pad
        bh = max(6, s.fh // 2)
        s.frame(ui.pad, y, bw, bh, DIM)
        fill = (bw - 2) * done // max(1, total)
        s.rect(ui.pad + 1, y + 1, fill, bh - 2, GOOD)
        y += bh + 4
        rate = done / max(0.1, now - self.start)
        s.text(ui.pad, y, "%s of %s" % (human(done), human(total)), TEXT, BG)
        y += s.fh + 1
        s.text(ui.pad, y, "%s/s" % human(rate), DIM, BG)
        ui.draw_footer("B stop")
        s.flush()
        return True
