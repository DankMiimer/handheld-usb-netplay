# Firmware notes

What USB Netplay relies on in each firmware, and where it comes from. Most of
this was read from the firmware sources in October 2026. Paths are relative
to the repository named in each heading. Items marked *(tested)* were
confirmed on a real handheld. Items marked *(unverified)* are inferred and
are worth checking on a device (`zcat /proc/config.gz`, `ls /sys/class/udc`).
Kernel options change between releases, which is why USB Netplay probes at
run time instead of trusting this table.

## Summary

| Firmware / SoC | USB device | USB host | Install folder | Service |
|---|---|---|---|---|
| ROCKNIX, all but RK3399 | own NCM mode (`usbgadget`) *(tested on RK3566, RK3326)* | yes *(tested)* | `/storage/.config/usb-netplay` | systemd, `/storage/.config/system.d` |
| ROCKNIX RK3399 | no | no NCM/RNDIS host driver | — | — |
| AmberELEC | no | yes | `/storage/.config/usb-netplay` | systemd, `/storage/.config/system.d` |
| KNULLI H700/A133 | ADB only *(tested on H700)* | yes *(tested on H700)* | `/userdata/system/usb-netplay` | `system.services` |
| KNULLI RK3566/RK3568/SM8250 | kernel has NCM/ECM/RNDIS | yes | same | same |
| KNULLI RK3326 | no gadget support | yes | same | same |
| Batocera H700/RK3568/SM8250 | kernel has NCM/ECM/RNDIS | yes (modules) | `/userdata/system/usb-netplay` | `system.services` |
| Batocera RK3326 | no configfs | yes | same | same |
| dArkOS/ArkOS RK3326 | only after a device tree edit | yes (modules) | `/opt/usb-netplay` | systemd, `/etc/systemd/system` |
| dArkOS RK3566 | probably not | yes *(unverified)* | same | same |
| muOS 2606+ | Device Link (muOS to muOS) | *(unverified)* | the app folder | User Init |
| SpruceOS | no network gadget | Flip maybe | — | — |
| Onion | no USB data | no | — | — |

## ROCKNIX (and JELOS)

[ROCKNIX/distribution](https://github.com/ROCKNIX/distribution), branch
`next`, paths under `projects/ROCKNIX/`.

- **USB mode tool:** `/usr/bin/usbgadget` (`packages/rocknix/sources/scripts/usbgadget`).
  - Modes are `disabled`, `network` (internally `cdc`) and `file_transfer`
    (`mtp`).
  - The network gadget is NCM, 1d6b:0104; the interface is renamed to
    `gadget`.
  - Its address comes from `/storage/.cache/usbgadget/network.conf` (`IP`,
    `NETMASK`, plus a `HOSTIP` line, which is IP minus one). `udhcpd` hands
    the host that address.
  - Defaults: 169.254.170.2/16 today, 10.1.1.2 on release 20250517 and
    earlier.
- **`disabled` switches the controller to host** (`set_role host`), unless a
  Type-C controller owns the role. On a cable to another host that leaves
  two hosts. *(tested: an RK3326 clone froze when switched off while
  plugged in)*
- **Boot:** `usbgadget --start` runs from an autostart script. It can stop
  partway, leaving the interface down or without its address. *(tested on
  RK3326)*
- **Kernels** (`devices/*/linux/linux.aarch64.conf`):
  - RK3326, RK3566, H700, S922X, RK3576, RK3588, SM* and SDM845 have
    configfs NCM, ECM and RNDIS, plus host `CDC_NCM`, `CDCETHER` and
    `RNDIS_HOST`. SDM845 lacks `RNDIS_HOST`.
  - RK3399 has no gadget network functions and no host `CDC_NCM` or
    `RNDIS_HOST`.
- **Persistent paths** (the root filesystem is read-only):
  - `/storage/.config/system.d` for systemd units.
  - `/storage/.config/udev.rules.d`, which `/etc/udev/rules.d` points to.
  - `/etc/hosts` is a symlink to `/run/rocknix/hosts` (tmpfs), rebuilt at boot
    (`packages/sysutils/systemd/scripts/network-base-setup`).
- **Network manager:**
  - NetworkManager since 2026-05. Its config already lists
    `unmanaged-devices=interface-name:gadget;usb0;usbnet;rndis0`
    (`packages/network/networkmanager/config/`).
  - connman in older releases. Its blacklist doesn't include USB;
    `/storage/.config/connman_main.conf` replaces `main.conf` entirely.
- **RetroArch:** `setsettings.sh` turns `system.cfg` keys into an append
  config:
  - `global.netplay=1` enables the rest.
  - `.port` becomes `netplay_ip_port`, and a `.relay` of none/false/0 turns
    the relay off.
  - `global.netplay_public_announce` controls announcing.
  - `retroarch.cfg` is `/storage/.config/retroarch/retroarch.cfg`, shared by
    the 32-bit build.
- **Ports:** `/storage/roms/ports/*.sh`, run as root with output to
  `/var/log/exec.log`. Tools (`/storage/.config/modules`) is re-synced at
  every boot, so dropped-in files vanish.
- **On screen:** `text_viewer`, `sdl2notify`, and the ES HTTP API on
  127.0.0.1:1234. python3 is present.

## AmberELEC

[AmberELEC/AmberELEC](https://github.com/AmberELEC/AmberELEC), branch `dev`
(last stable release 20230203).

- **No USB network gadget.**
  - RG351P/V/MP (RK3326, 4.4): DWC2 is host-only and DWC3 isn't built, so
    there is no device controller.
  - RG552: no network gadget functions.
  - RG353/RG503: configfs has only FunctionFS, ACM, mass storage and UVC.
  - Host drivers (`CDC_NCM`, `CDCETHER`, `RNDIS_HOST`) are present on all.
- **Persistent paths:** `/storage/.config/system.d`,
  `/storage/.config/udev.rules.d`, and `/storage/.config/custom_start.sh`
  (`packages/amberelec/sources/autostart.sh`).
- **connman** on all builds; override with `/storage/.config/connman_main.conf`.
- **RetroArch:** `setsettings.py` reads `distribution.conf`. **Any
  `global.netplay.relay` value other than empty or `0`, `none` included,
  turns the relay on.**
- **Ports:** `/storage/roms/ports`, output to `/tmp/logs/exec.log`. For text,
  use `text_viewer` or the console. The ES HTTP API exists in AmberELEC's ES
  (`es-app/src/services/HttpServerThread.cpp` in
  [AmberELEC/emulationstation](https://github.com/AmberELEC/emulationstation)).

## KNULLI

[knulli-cfw/distribution](https://github.com/knulli-cfw/distribution), branch
`knulli-main`.

- **USB mode:** `board/batocera/<soc>/fsoverlay/etc/init.d/S28usbmode`,
  for a133, h700, rk3128 and rk3566 only.
  - It builds configfs gadget `g1` (1d6b:0104, "KNULLI CFW") with `ffs.adb`
    for ADB, or `mass_storage` for "mtp".
  - There is no network mode.
- **ADB on H700** *(tested)*:
  - adbd is old: `shell:` and `tcp:` work; there is no `reverse:` and no
    authentication.
  - At every unplug, adbd restarts its USB side and `g1` is unbound (`udc`
    `change` event). S28usbmode binds it only at boot.
- **Kernels** (`configs/knulli-<soc>.board` and the config files they name):

  | SoC | Network gadget | Host drivers |
  |---|---|---|
  | H700/A133, 4.9 BSP | none (only mass storage and FunctionFS) | `CDC_NCM`, `CDCETHER`, `CDC_SUBSET`; no `RNDIS_HOST` |
  | RK3566 | NCM, ECM, RNDIS | `CDC_NCM`, `RNDIS_HOST`, `CDCETHER` |
  | RK3568 | everything | everything |
  | SM8250 | NCM, ECM, RNDIS, EEM (modules) | `CDC_NCM`, `CDCETHER` (modules) |
  | RK3326 | `CONFIG_USB_GADGET` off | modules |

- **Persistent paths:**
  - `/userdata/system/services/<name>` (start/stop script), enabled by name
    in `system.services`.
  - `/userdata/system/udev/rules.d`.
  - `/boot/boot-custom.sh` runs early at boot, before connman.
  - `/etc/hosts` is rewritten at boot by `S26system`.
- **connman:** the default blacklist doesn't include USB. With
  `SingleConnectedTechnology=true`, a USB link replaces Wi-Fi unless it is
  blacklisted. *(tested)*
- **RetroArch:**
  - `global.retroarch.<key>` in `knulli.conf` is copied into
    `retroarchcustom.cfg` on every launch.
  - `global.netplay=1` enables the netplay menu.
  - The default NES core is FCEUmm (ROCKNIX uses Nestopia).
- **Ports and Tools** run as `/bin/bash script`, with output to
  `es_launch_stdout.log`. On-screen text comes from the ES HTTP API,
  `POST http://127.0.0.1:1234/messagebox`, in the
  [KNULLI ES fork](https://github.com/knulli-cfw/batocera-emulationstation).
- python3 is present; busybox `udhcpc` is not.

## Batocera

[batocera-linux/batocera.linux](https://github.com/batocera-linux/batocera.linux),
branch `master`.

- **No USB mode tool** and no adbd: a gadget has to be built in configfs.
- **Kernels:**
  - H700 has configfs NCM, ECM, RNDIS and EEM, with host drivers as modules.
  - RK3568 has everything.
  - SM8250 has NCM, ECM and RNDIS.
  - RK3326 has `USB_GADGET` but not `USB_CONFIGFS`.
- **Services:** `S99userservices` runs `/userdata/system/services/*` that are
  listed in `system.services` (also `batocera-services enable`).
  `/boot/boot-custom.sh` is run by `S00bootcustom`.
- **RetroArch:** `retroarch.*` keys go into `retroarchcustom.cfg`, the same
  as on KNULLI.

## dArkOS and ArkOS

[christianhaitian/dArkOS](https://github.com/christianhaitian/dArkOS); ArkOS
([christianhaitian/arkos](https://github.com/christianhaitian/arkos)) is
archived in its favour.

- **RK3326 kernel** ([christianhaitian/linux](https://github.com/christianhaitian/linux),
  branch `rg351`):
  - `g_ether` with RNDIS and `g_ncm` are available as modules; configfs has
    ECM, RNDIS and EEM.
  - Host `CDC_NCM`, `CDCETHER` and `RNDIS_HOST` are modules.
  - The board device trees disable the OTG port's role detection, so the
    port stays a host. Making it a device needs a device-tree edit
    (Just-Luis's
    [RK3326-USB-Netplay](https://github.com/Just-Luis/RK3326-USB-Netplay)
    does that). USB Netplay never edits device trees.
- **RK3566 kernel:** configfs lists only mass storage and FunctionFS
  *(unverified)*.
- **System:**
  - The root filesystem is writable.
  - User `ark` has passwordless sudo; Ports run as ark, so `usbnp`
    re-runs itself with `sudo -n`.
  - udev rules go in `/etc/udev/rules.d`.
  - python3 is present.
- **NetworkManager:** `/etc/NetworkManager/NetworkManager.conf` has an
  `unmanaged-devices` list, which USB Netplay extends.
- **RetroArch:** `/home/ark/.config/retroarch/retroarch.cfg` and
  `/home/ark/.config/retroarch32/retroarch.cfg`.
- **On screen:** Tools send stdout to tty1. Ports must write to `/dev/tty0`
  or `/dev/tty1` themselves.

## muOS

[MustardOS/internal](https://github.com/MustardOS/internal) and
[MustardOS/frontend](https://github.com/MustardOS/frontend), release 2606.

- **Device Link** (`settings/network/usb_cable`): a vendor-class FunctionFS
  pipe, gadget `mulink` (1d6b:0110), bridged to a TAP interface `usblink0`,
  from `frontend/common/platform/usb_link.c` and `frontend/module/mulink.c`.
  - It pairs two muOS handhelds and gives them 169.254.X.1/.2. Pairing state
    is in `/run/muos/network/link`.
  - RetroArch's LAN discovery ignores 169.254, so USB Netplay adds a
    10.250.X.Y address on the same link.
- **ADB/MTP** (`usb_function`): a watchdog rebinds the controller every
  second, so an own gadget needs `usb_function=0`.
- **User Init scripts** (`MUOS/init/*.sh`) run only with Settings > Advanced >
  User Init on.
- **Apps:** `MUOS/application/<Name>/mux_launch.sh`, run as root. Text goes
  through `/opt/muos/frontend/muxmessage`.
- **RetroArch:** `/opt/muos/share/info/config/retroarch.cfg`.
- Kernel network gadget and host options are *unverified*.

## SpruceOS and Onion

- **SpruceOS** ([spruceUI/spruceOS](https://github.com/spruceUI/spruceOS)):
  its USB gadget code offers mass storage only, and the A30 has no configfs.
  It has no user boot hook. The Flip can host USB Wi-Fi dongles, so it might
  host a cable link *(unverified)*. Not supported for now.
- **Onion** ([OnionUI/Onion](https://github.com/OnionUI/Onion)): the Miyoo
  Mini's USB-C port is reported to carry power only, and netplay there is
  Wi-Fi hotspot only. Not supported.

## RetroArch

[libretro/RetroArch](https://github.com/libretro/RetroArch),
`network/netplay/netplay_frontend.c`. Tested with 1.22.2.

- **LAN discovery:**
  - The client sends one UDP query to 255.255.255.255:55435.
  - The host answers only queries from private addresses (10/8, 172.16/12,
    192.168/16). Link-local 169.254 doesn't count.
- **Connect to Netplay Host** connects to `netplay_ip_address` directly and
  keeps the current core. A join from the host list loads the host's core.
- **Netplay needs the same core build on both ends.** Different versions of
  the same core can connect and then desync. *(tested: Nestopia 1.52 against
  1.54)*
