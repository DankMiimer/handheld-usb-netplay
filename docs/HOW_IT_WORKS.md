# How USB Netplay works

This page is for people who want to know what the script does to their
handheld, or who want to add a firmware. The code is in `usb-netplay/`:

| File | What it does |
|---|---|
| `usbnp` | the commands: `menu`, `setup`, `remove`, `status`, `watch`, `link-up`, `link-down` and a few internal ones |
| `lib/detect.sh` | which firmware this is, where to install, the handheld's id |
| `lib/caps.sh` | what this handheld can be on the cable |
| `lib/net.sh` | the link itself, on both ends |
| `lib/common.sh` | logging, the dry-run hook, and editing files with a record of every change |
| `lib/fw/*.sh` | one file per firmware: service, network manager, RetroArch settings |
| `lib/ui.sh` | showing the report on the screen |
| `lib/dhcp_probe.py` | asks the other handheld's DHCP server for an address |
| `adb/tunnel.py`, `adb/helper.py` | the ADB tunnel for handhelds that can only be an ADB device |

Everything runs as root under a plain POSIX shell (busybox ash, dash or bash).
Python 3 is needed only for the DHCP probe and the ADB tunnel.

## 1. What each handheld can be

A USB link has a **host**, the side with the OTG adapter that powers the
cable, and a **device**. `caps_probe` decides both roles at run time and
never switches a USB role itself:

- **Host:** the handheld has a USB host bus (`/sys/bus/usb/devices/usbN`).
  The other handheld then shows up as a `cdc_ncm`, `cdc_ether`, `rndis_host`,
  `cdc_eem` or `cdc_subset` network interface.
- **Device**, the first that applies:
  1. `native`: ROCKNIX's own USB network mode (`usbgadget`, an NCM gadget).
  2. `adb`: a firmware gadget with a FunctionFS ADB function, as on KNULLI.
     It is left alone, and the host on the other end tunnels through it.
  3. Another firmware gadget (file transfer, mass storage) holds the
     controller: left alone, with a hint to set the USB mode to off.
  4. `own`: the kernel can make a network function. Probed by creating
     `ncm`, `ecm` and `rndis` in a scratch configfs gadget, which is removed
     again straight away. USB Netplay then builds its own gadget, `usbnp`.
  5. `devlink`: muOS's own Device Link.
  6. `none`.

Known dead ends say so plainly and change nothing: ROCKNIX on RK3399,
Onion, SpruceOS, and unknown firmware.

## 2. Addresses

Each handheld has its own /24 subnet, `10.H1.H2.0/24`. It comes from the md5
of its machine id: H1 is 160 + n mod 32, H2 is (n / 32) mod 256. That gives
8192 subnets, so two handhelds clash only by bad luck. When the handheld is
the USB device it is `.2`, and the host on the other end gets `.1`. The
ranges are private, because RetroArch only answers LAN discovery from
10/8, 172.16/12 and 192.168/16. That rules out the link-local 169.254/16
some firmwares use by default.

- **ROCKNIX** as the device: USB Netplay writes `IP` and `NETMASK` into
  `/storage/.cache/usbgadget/network.conf`. ROCKNIX's `udhcpd` then hands the
  host `.1`.
- **Own gadget:** the address is in the USB serial number, `usbnp-10.H1.H2.2`,
  so the host needs no DHCP.

## 3. Host side: finding the other handheld

When a USB network interface appears, `link_up` tries these in order:

1. **The USB serial number.** A USB Netplay gadget carries its address there.
2. **DHCP.** `dhcp_probe.py` sends one DHCP DISCOVER on a raw packet socket,
   which works on an interface that has no address yet, and reads the offer.
   The server is the other handheld, and the offered address is ours. Nothing
   is leased and no DHCP client is started (KNULLI has none anyway).
3. **Known static gadget addresses:** `10.170.0.5` (the RG Nano's USB Link
   app), `169.254.170.2` (ROCKNIX default) and `10.1.1.2` (JELOS). Each is
   tried with a ping.

The host then sets only:

```
ip addr replace ME/32 dev usb0
ip route replace PEER/32 dev usb0 src ME
ip route replace 255.255.255.255/32 dev usb0
```

There is no default route and no DNS, so Wi-Fi keeps working. The /32 means
two cable links on one handheld never clash. The RG DS, for example, can be
a device on one port and a host on the other. The broadcast route makes
RetroArch's LAN host list search (a single `sendto 255.255.255.255`) go over
the cable.

## 4. The `usb-netplay` name

RetroArch's pre-filled netplay host is set to the name `usb-netplay`, and USB
Netplay keeps that name in the hosts file pointed at the right place:

- the other handheld of the newest cable link;
- the host on this handheld's own gadget, while one is connected;
- `127.0.0.1` while the ADB tunnel is up;
- otherwise `FALLBACK_HOST` from `settings.conf`, or nothing.

On ROCKNIX the hosts file is `/run/rocknix/hosts` (tmpfs), and on KNULLI it is
rebuilt at every boot. That is why the background loop re-applies the name.

## 5. The ADB tunnel (KNULLI H700 and A133)

KNULLI's 4.9 Allwinner kernel can't be a USB network device. Its ADB gadget
works, but its adbd is old: it has `shell:` and `tcp:`, no `reverse:`, and no
authentication. So the host side does the work.

1. `tunnel.py` runs on the host side, under the background loop. It looks
   for a USB interface of class ff/42/01 and talks ADB to it through usbfs
   ioctls in pure Python. No adb binary is needed.
2. If the device asks for authentication (a phone, say), the tunnel leaves
   it alone. Otherwise it uses `shell:` to look for USB Netplay's helper at
   the known install paths, and stops if it isn't installed there.
3. It starts `helper.py` on the device. The helper listens on
   `127.0.0.1:55435` (RetroArch's port) and on a control port reached
   through adbd's `tcp:55439`, and points `usb-netplay` at `127.0.0.1`.
4. When RetroArch on the device joins `usb-netplay`, the helper reports the
   new connection over the control channel. The tunnel opens a second
   `tcp:` stream for it and connects it to the host's own
   `127.0.0.1:55435`, where RetroArch is hosting.

So over ADB the device side always joins and the host side always hosts. The
helper holds port 55435 on the device. If the helper hears nothing from the
tunnel for 20 seconds, it exits and the name falls back.

KNULLI unbinds its ADB gadget at every unplug, and its boot script only binds
it once. A udev rule on the `udc` `change` event binds it again while adbd is
running, so the tunnel comes back after a replug.

## 6. Staying up: udev and the background loop

- **A udev rule** (`90-usb-netplay.rules`) matches USB network interfaces
  coming and going, and only wakes the background loop. systemd's udevd
  kills anything a rule starts once the event is handled, and finding the
  other handheld can take seconds, so the real work isn't done there.
- **The background loop** (`usbnp watch`) runs as a service and wakes every
  5 seconds, or at once when poked. It:
  - sets up interfaces that have no link yet, and drops links whose
    interface is gone. Where nothing answered, it tries again after 10 s,
    then every 30 s. The RG Nano's USB Link, for one, sets its address only
    after the host has connected;
  - sets a link up again if its address was lost, and looks again after 3
    failed pings, for example when the other handheld was re-addressed;
  - keeps ROCKNIX's gadget up with its address and DHCP server, because
    ROCKNIX's boot sometimes leaves it down;
  - keeps its own gadget bound;
  - runs the ADB tunnel.
- **The service** depends on the firmware:

  | Firmware | Service |
  |---|---|
  | ROCKNIX, AmberELEC | a systemd unit in `/storage/.config/system.d`. It is `Type=simple`, `WantedBy=multi-user.target`, and **has no ordering**: `After=rocknix-autostart.service` creates an ordering cycle that boots to a black screen. |
  | KNULLI, Batocera | `/userdata/system/services/usb_netplay`, listed in `system.services` |
  | dArkOS | a systemd unit in `/etc/systemd/system` |
  | muOS | a User Init script |

## 7. Network managers

A network manager that sees the cable interface may try DHCP on it, or (on
KNULLI) drop Wi-Fi in favour of it.

- **NetworkManager:** ROCKNIX's config already leaves `gadget`, `usb0`,
  `usbnet` and `rndis0` alone. dArkOS gets `usb*` and `rndis*` added to
  `unmanaged-devices`. Other names get `nmcli device set IF managed no`.
- **connman:** on ROCKNIX and AmberELEC, `usb`, `gadget` and `rndis` are added
  to `NetworkInterfaceBlacklist` in `/storage/.config/connman_main.conf`,
  which is a copy of the stock `main.conf`. On KNULLI and Batocera they are
  added by a block in `/boot/boot-custom.sh`, because the main config is
  rebuilt at boot. Either way, connman reads it at the next start, which is
  why the status asks for one restart.

## 8. RetroArch settings

LAN only: announcing off, NAT traversal off, relay off, port 55435, and the
pre-filled host `usb-netplay`. Each firmware's own settings path is used, so
its launcher doesn't overwrite the values:

| Firmware | Where the settings go |
|---|---|
| ROCKNIX | `system.cfg` (`global.netplay*`) and `retroarch.cfg` |
| AmberELEC | `distribution.conf` (relay `0`, because any other value, `none` included, turns the relay on) and `retroarch.cfg` |
| KNULLI, Batocera | `global.retroarch.*` keys in the `.conf`, which the launcher copies into `retroarchcustom.cfg` |
| dArkOS | both `retroarch.cfg` and `retroarch32/retroarch.cfg`, owned by `ark` |
| muOS | its `retroarch.cfg` |

Cores are never copied. The status screen lists md5 checksums of the common
netplay cores so you can compare the two handhelds.

## 9. Undoing everything

Every change goes through `put_file`, `set_key` or `add_block`, each of which
first adds a line to `state/manifest`:

- a backup of a whole file;
- a key's old value, or the fact that it was missing;
- a marked block (`# >>> usb-netplay` ... `# <<< usb-netplay`).

`remove` replays the manifest in reverse. The tests check that setup followed
by remove leaves each fake firmware tree byte for byte as it was.

## 10. Lessons from the real handhelds

- systemd 255's udevd kills processes started by `RUN` once the event is
  done, so rules only poke the loop.
- On ROCKNIX, a unit that is `WantedBy=multi-user.target` and
  `After=rocknix-autostart.service` creates an ordering cycle, and the
  handheld boots to a black screen.
- ROCKNIX's `usbgadget --start` can stop partway at boot and leave the
  interface down or without its address. The loop puts the address back.
- KNULLI's g1 ADB gadget is unbound at every unplug (a `udc` `change`
  event), not removed, so a rule on `add` never fires.
- On Allwinner H700 kernels, never read
  `/sys/devices/platform/soc/usbc0/usb_host`, `usb_device` or `usb_null`.
  Reading them switches the USB role and can hang. `otg_role` is safe.
- **Connect to Netplay Host** doesn't switch cores the way the host list
  does, so different default cores ("host is running another core") show up
  there first.
