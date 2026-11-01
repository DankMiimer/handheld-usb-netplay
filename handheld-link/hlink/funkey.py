"""FunKey OS (DrUm78's builds): which handheld this is, its screen, its USB port.

The RG Nano's port can be a USB host (the Wi-Fi dongle uses that too); the
GBA Mini's can only be a device, and its panel shows only 160-odd rows of
the 240x240 framebuffer.
"""

from __future__ import annotations

import glob
import os
import signal
import subprocess

MUSB = os.environ.get("HLINK_MUSB", "/sys/devices/platform/soc/1c19000.usb/musb-hdrc.1.auto")
POWER = os.environ.get("HLINK_POWER", "/sys/class/power_supply/axp20x-usb")
FKGPIOD = "/etc/fkgpiod.conf"
USBLINK_WATCH_PID = "/tmp/usblink-watch.pid"
USBLINK_WATCH = "/tmp/usblink-watch.sh"


def _read(path: str, default: str = "") -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def is_gba_mini() -> bool:
    # DrUm78's GBA Mini OS maps Start+A/B to X/Y: it has no X and Y buttons
    return "START+A" in _read(FKGPIOD).replace(" ", "")


def can_host() -> bool:
    """The port's controller registered a host side (a USB root hub)."""
    return bool(glob.glob(os.path.join(MUSB, "usb[0-9]*")))


def model() -> str:
    if is_gba_mini():
        return "GBA Mini"
    return "RG Nano" if can_host() else "FunKey S"


def screen() -> tuple[int, int, int, int]:
    """The part of the framebuffer the panel shows: x, y, width, height."""
    want = os.environ.get("HLINK_SCREEN", "")
    if want:
        try:
            size, x, y = want.split("+")
            w, h = size.split("x")
            return int(x), int(y), int(w), int(h)
        except ValueError:
            pass
    if is_gba_mini():
        # measured with numbered stripes: rows 48 to about 210 show
        return 0, 48, 240, 160
    return 0, 0, 240, 240


def rebind_gadget() -> str:
    """Unbind and bind the USB gadget again; returns what was done, for the log.

    The GBA Mini's USB device side has no way to see a cable come and go:
    after an unplug it stays "not attached" for good, even for a new host,
    until the gadget is bound again (or the handheld reboots)."""
    for g in glob.glob("/sys/kernel/config/usb_gadget/*/UDC"):
        udc = _read(g)
        if not udc:
            names = sorted(os.listdir("/sys/class/udc")) if os.path.isdir("/sys/class/udc") else []
            if not names:
                continue
            udc = names[0]
        try:
            with open(g, "w") as f:
                f.write("\n")
        except OSError:
            pass
        try:
            with open(g, "w") as f:
                f.write(udc)
            return "rebound %s to %s" % (os.path.basename(os.path.dirname(g)), udc)
        except OSError as e:
            return "could not rebind %s: %s" % (g, e)
    return "no USB gadget to rebind"


class Port:
    """The USB port, for the handheld that hosts."""

    def mode(self) -> str:
        return _read(os.path.join(MUSB, "mode"))

    def is_host(self) -> bool:
        return self.mode().startswith("a_")

    def usb_power(self) -> bool:
        """A PC or charger on the port (only meaningful in device mode)."""
        return _read(os.path.join(POWER, "online"), "0") == "1"

    def vbus(self) -> float | None:
        """The voltage the power chip measures on the port, for the log."""
        v = _read(os.path.join(POWER, "voltage_now"))
        return int(v) / 1e6 if v.isdigit() else None

    def __init__(self, log=lambda s: None) -> None:
        self.log = log
        self.stopped_watcher = False
        self.forced = False

    def claim(self) -> None:
        """The port is a host: keep USB Link's watcher, which would rebuild
        the USB gadget under it, away until release()."""
        pid = _read(USBLINK_WATCH_PID)
        if pid.isdigit():
            try:
                os.kill(int(pid), signal.SIGTERM)
                self.stopped_watcher = True
                self.log("port: stopped USB Link's watcher")
            except OSError:
                pass
            try:
                os.unlink(USBLINK_WATCH_PID)
            except OSError:
                pass

    def to_host(self) -> str | None:
        """Make the port a host when an OTG adapter didn't; returns why not, or None."""
        if self.is_host():
            return None
        if self.usb_power():
            return "A PC or charger is on the USB port"
        self.claim()
        try:
            with open(os.path.join(MUSB, "mode"), "w") as f:
                f.write("host")
        except OSError as e:
            return "Can't switch the port: %s" % (e.strerror or e)
        self.forced = True
        self.log("port: switched to host mode")
        return None

    def release(self) -> None:
        """Undo to_host: back to "otg", where the port is a device unless an
        OTG adapter is plugged in (how it starts at boot)."""
        if self.forced:
            try:
                with open(os.path.join(MUSB, "mode"), "w") as f:
                    f.write("otg")
                self.log("port: back to otg (a device unless an adapter is plugged in)")
            except OSError as e:
                self.log("port: can't switch back: %s" % e)
            self.forced = False
        if self.stopped_watcher and os.path.exists(USBLINK_WATCH):
            subprocess.Popen(["setsid", "sh", USBLINK_WATCH, "--watch"], cwd="/",
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self.stopped_watcher = False
            self.log("port: started USB Link's watcher again")
