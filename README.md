# USB Netplay

RetroArch netplay between two Linux retro handhelds over a USB cable instead
of Wi-Fi. Install it on both handhelds, run it once from the menu, plug the
cable in, then host and join as usual. The cable gives about 2 ms ping,
where the same pair over Wi-Fi measured about 28 ms. It needs no router, and
it works on handhelds without Wi-Fi, such as R36S clones.

It is a menu script, not a firmware patch. It works out what each handheld
can do, picks the right way to link them, and lets RetroArch find the other
handheld by itself. **USB Netplay - Remove** undoes every change it made.

## What you need

- Two handhelds on firmware from the [support table](#which-handhelds-work).
  At least one of them must be able to act as the **USB device**.
- An **OTG adapter** (USB-C or micro-USB male to USB-A female) and a normal
  USB-A cable to the other handheld's data port.
- The **same game and the same core build** on both. Netplay desyncs or
  refuses to connect otherwise. USB Netplay's status screen lists the core
  checksums so you can compare them.

### Cable safety

- **The OTG adapter goes on the USB host**, the handheld that powers the link.
  The other handheld is the USB device.
- **Don't link two handhelds with a plain USB-C to USB-C cable.** Most
  handhelds don't negotiate USB-C roles properly, so neither side decides who
  supplies 5 V or who is the host. The adapter settles both.
- On handhelds with two USB-C ports, use the right one. The Anbernic RG DS,
  for example, can only be the USB device on its charging port and only the
  host on its other port.
- **Unplug before you switch a handheld's USB networking off.** With the
  cable in, a ROCKNIX R36S clone froze when its USB mode was turned off,
  because that makes both ends USB hosts.

## Install

Download `usb-netplay-<version>.zip` from the
[latest release](https://github.com/DankMiimer/handheld-usb-netplay/releases/latest).
Unpack it into the **Ports** folder, so that `USB Netplay.sh`,
`USB Netplay - Remove.sh` and the `usb-netplay` folder sit side by side.

| Firmware | Ports folder |
|---|---|
| ROCKNIX, JELOS, AmberELEC | `roms/ports` on the games card (`/storage/roms/ports`) |
| KNULLI, Batocera | `roms/ports` on the share (`/userdata/roms/ports`) |
| dArkOS, ArkOS | `ports` on the EASYROMS card (`/roms/ports`), or `tools`, which shows under Options > Tools |
| muOS (experimental) | make a folder `MUOS/application/USB Netplay/`, put the `usb-netplay` folder in it, and copy `USB Netplay.sh` there as `mux_launch.sh` |

If the entries don't show up, refresh the game list (KNULLI and Batocera:
Game Settings > Update Gamelists) or restart the frontend.

## How to play

1. On **both** handhelds, open **Ports > USB Netplay**. It sets itself up and
   shows a status screen (where it can, the screen closes by itself after 25
   seconds). If the status says *Restart this handheld once*, do that.
   On a ROCKNIX handheld that will be the USB device, also set its USB mode
   to **network** in its network settings. USB Netplay sets the address.
2. Plug the cable in, with the OTG adapter on the host side.
3. On one handheld, start the game and choose
   **RetroArch menu > Netplay > Host**.
4. On the other handheld, start the same game with the same core and choose
   **Netplay > Connect to Netplay Host**. The address is already filled in:
   it is the name `usb-netplay`, which always points at the handheld on the
   other end of the cable. **Refresh Netplay Host List** works too, and it
   also switches to the host's core.

With the ADB tunnel (a KNULLI H700 or A133 handheld as the USB device), only
one direction works: the handheld with the OTG adapter hosts, and the KNULLI
handheld joins.

Run **USB Netplay** again whenever you like. It repairs its setup and shows
the link, the other handheld's address and the ping. The join message tells
you which way you connected: a ping of about 2 ms means the cable, 20 ms and
up means Wi-Fi. A game that is already running stays on Wi-Fi until you
rejoin.

### Settings (optional)

Copy `usb-netplay/settings.example.conf` to `usb-netplay/settings.conf` and
edit the copy, so that updates don't overwrite it. Then run USB Netplay
again.

- `FALLBACK_HOST=` is where `usb-netplay` points while no cable is plugged
  in, for example the Wi-Fi address of the handheld you usually play against.
  Leave it empty and **Connect to Netplay Host** only works over the cable
  (the host list still works on Wi-Fi).

### Removing it

Run **Ports > USB Netplay - Remove**. It restores every file it changed from
its own record of the changes, then deletes its installed copy. You can then
delete the files from Ports.

## Which handhelds work

A link always has one **USB host** (the side with the OTG adapter) and one
**USB device**. USB Netplay checks what each handheld can do when it runs,
and says so plainly if a handheld can't take part.

**Tested** on real hardware, with a real netplay join over the cable:

| USB host (OTG adapter) | USB device | How they link | Also tested |
|---|---|---|---|
| ROCKNIX, RK3566 (Anbernic RG DS) | ROCKNIX, RK3326 (R36S clone, AurkNix build) | ROCKNIX's own USB network gadget, address by DHCP | replug, reboot with the cable in |
| ROCKNIX, RK3566 (Anbernic RG DS) | KNULLI, H700 (Anbernic RG34XX-SP) | ADB tunnel (the H700 kernel has no USB network gadget); the RG DS hosts | replug, reboot of both* |
| KNULLI, H700 (Anbernic RG34XX-SP) | ROCKNIX, RK3326 (R36S clone) | ROCKNIX's own USB network gadget, address by DHCP | |

\* The replug and reboot of the RG DS and RG34XX-SP pair were tested with the
hand-built setup this tool grew out of. With the tool itself, that pair was
tested with a join.

**Expected to work, from the firmware source code (untested):**

| Firmware | Can be the USB device | Can be the USB host | Notes |
|---|---|---|---|
| ROCKNIX / JELOS, every SoC except RK3399 | yes, its own USB network mode | yes | ROCKNIX's USB mode must be *network* (USB Netplay sets the address) |
| KNULLI H700, A133 | yes, through ADB | yes | the USB mode must stay *ADB* (the default), and this handheld joins rather than hosts. The host on the other end needs python3 (ROCKNIX, AmberELEC, KNULLI and dArkOS all have it) |
| KNULLI RK3566, RK3568, SM8250 | yes, own gadget | yes | with KNULLI's USB mode off; in ADB mode it works like the H700 |
| Batocera H700, RK3568, SM8250 | yes, own gadget | yes | |
| AmberELEC | no | yes | |
| dArkOS / ArkOS RK3326 | no | yes | the OTG port is host-only unless the device tree is edited, which USB Netplay never does |
| dArkOS RK3566 | no | yes | |
| KNULLI / Batocera RK3326 | no | yes | the kernel has no USB gadget support |
| muOS 2606+ | muOS Device Link only (muOS to muOS) | probably | experimental; the boot hook needs Settings > Advanced > User Init |

**Not supported:**

- ROCKNIX on RK3399: no USB network gadget and no NCM host driver.
- SpruceOS: no USB network gadget and no boot hook.
- Onion (Miyoo Mini): the USB-C port carries no USB data.
- Two handhelds that can only be the host, such as an AmberELEC and a dArkOS
  handheld. One side must be able to be the USB device.
- The RG Nano (FunKey OS) has no RetroArch netplay. Its
  [USB Link](https://github.com/DankMiimer/rg-nano-adb-tools) app (with
  `NETWORK=ncm`) can still bring up a cable link to it. USB Netplay finds the
  Nano at its default address. Tested with an RG DS as the host: the link was
  up 8 seconds after the Nano's network interface appeared, ping about 2 ms.

Tried it on another handheld? Please open an issue with `last-run.txt` from
the `usb-netplay` folder, whether it worked or not.

## Troubleshooting

- **"Host is running another core".** **Connect to Netplay Host** keeps the
  core you started with. Set the same default core on both handhelds (for
  example Nestopia for NES; KNULLI defaults to FCEUmm, ROCKNIX to Nestopia),
  or join from **Refresh Netplay Host List**, which switches cores.
- **The game desyncs or jumps.** The two handhelds have different builds of
  the same core. Compare the checksums at the bottom of the status screen and
  copy one handheld's core to the other.
- **Nothing on the other end.** Check that the OTG adapter is on the host and
  that you used the data port. Then open USB Netplay on both and read
  *Links now*.
- **It joined over Wi-Fi.** The cable wasn't linked yet when you joined. Run
  USB Netplay to check the link, then rejoin.
- **Logs.** The installed copy lives in `/storage/.config/usb-netplay`
  (ROCKNIX, AmberELEC), `/userdata/system/usb-netplay` (KNULLI, Batocera) or
  `/opt/usb-netplay` (dArkOS). Its `usbnp.log` records every link change, and
  `last-run.txt` has the last status report.

## How it works

The short version:

1. The USB device side gets its own small subnet, picked from a hash of the
   handheld's machine id, and hands the host an address.
2. The host side finds the other handheld in one of three ways: from the USB
   serial number, by asking its DHCP server, or by trying known default
   addresses.
3. The host side adds only a /32 address and host routes, so Wi-Fi and its
   default route stay as they are.
4. A broadcast route makes RetroArch's LAN host list search over the cable.
5. A hosts entry, `usb-netplay`, points at whichever handheld is on the other
   end.

KNULLI's H700 kernel can't be a USB network device. For it, a small ADB
client on the host side tunnels the netplay connection through the firmware's
own ADB gadget. A udev rule and a background loop handle plug-ins, unplugs
and reboots.

The long version is in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md), and the
per-firmware research with sources is in
[docs/FIRMWARE_NOTES.md](docs/FIRMWARE_NOTES.md).

## Development

```sh
sh tests/run.sh    # shellcheck, dash syntax, shell tests on fake firmware trees, python tests
USBNP_TEST_SHELL="busybox ash" sh tests/run.sh
```

The shell tests run `usbnp` against fake copies of each firmware's files
(`tests/fixtures.sh`) with a dry-run hook, so nothing on the test machine is
touched. They check that setup followed by remove leaves every tree exactly
as it was.

## Credits

- The idea of cable netplay between handhelds came from Just-Luis's
  [RK3326-USB-Netplay](https://github.com/Just-Luis/RK3326-USB-Netplay)
  (ArkOS/dArkOS, by editing the device tree). This is a separate project with
  a different approach, and not affiliated with it.
- Made with the help of an LLM coding assistant (Anthropic's Claude), under my
  direction, and tested on my own handhelds.

MIT licence, see [LICENSE](LICENSE).
