# Fake firmware trees for the tests. Sourced by tests/test_usbnp.sh.
# make_root NAME DIR builds DIR as a tiny copy of what usbnp looks at on that
# firmware (paths and file formats from the real devices and sources).
# shellcheck shell=bash

w() { mkdir -p "$(dirname "$1")"; printf '%s' "$2" > "$1"; }

common_tree() {
    local d=$1
    mkdir -p "$d/sys/bus/usb/devices/usb1" "$d/sys/class/net" "$d/etc"
    w "$d/etc/hosts" $'127.0.0.1\tlocalhost\n'
}

with_udc() {   # a USB device controller and an empty configfs
    mkdir -p "$1/sys/class/udc/$2" "$1/sys/kernel/config/usb_gadget"
}

make_root() {
    local name=$1 d=$2
    rm -rf "$d"
    common_tree "$d"
    case "$name" in
        rocknix-nm|rocknix-connman|rocknix-rk3399)
            local soc=RK3566
            [ "$name" = rocknix-rk3399 ] && soc=RK3399
            [ "$name" = rocknix-connman ] && soc=RK3326
            w "$d/etc/os-release" $'NAME="ROCKNIX"\nOS_NAME="ROCKNIX"\nOS_VERSION="20261001"\nHW_DEVICE="'"$soc"$'"\n'
            mkdir -p "$d/run/rocknix"
            mv "$d/etc/hosts" "$d/run/rocknix/hosts"
            ln -s /run/rocknix/hosts "$d/etc/hosts"
            w "$d/usr/bin/usbgadget" $'#!/bin/sh\n'
            w "$d/storage/.cache/systemd-machine-id" "00112233445566778899aabbccddeeff"
            w "$d/storage/.config/system/configs/system.cfg" $'global.netplay.nickname=ROCKNIX Player\nglobal.netplay.port=55435\nglobal.netplay.relay=none\nglobal.netplay=0\naudio.volume=45\n'
            w "$d/storage/.config/retroarch/retroarch.cfg" $'netplay_ip_address = ""\nnetplay_ip_port = "55435"\nnetplay_nat_traversal = "true"\nnetplay_public_announce = "true"\nnetplay_use_mitm_server = "false"\nvideo_driver = "gl"\n'
            w "$d/storage/.cache/usbgadget/network.conf" $'IP=169.254.170.2\nNETMASK=255.255.0.0\n# Stupid calculator just subtracting 1 from last octet.\nHOSTIP=${IP%.*}.$((${IP##*.}-1))\n'
            if [ "$name" = rocknix-connman ]; then
                w "$d/etc/connman/main.conf" $'[General]\nPreferredTechnologies = ethernet\nNetworkInterfaceBlacklist = vmnet,vboxnet,virbr,ifb,docker,veth,zt,p2p\n'
            else
                w "$d/etc/NetworkManager/NetworkManager.conf" $'[keyfile]\nunmanaged-devices=interface-name:gadget;interface-name:usb0\n'
            fi
            with_udc "$d" fcc00000.usb
            w "$d/tmp/cores/nestopia_libretro.so" 'nestopia 1.54'
            w "$d/tmp/cores/fceumm_libretro.so" 'fceumm rocknix build'
            w "$d/storage/.config/emulationstation/es_systems.cfg" '<systemList>
  <system>
    <name>nes</name>
    <emulators><emulator name="retroarch"><cores>
      <core default="true">nestopia</core>
      <core>fceumm</core>
    </cores></emulator></emulators>
  </system>
</systemList>
'
            ;;
        amberelec)
            w "$d/etc/os-release" $'NAME="AmberELEC"\nVERSION="20230203"\n'
            w "$d/storage/.cache/systemd-machine-id" "0123456789abcdef0123456789abcdef"
            w "$d/storage/.config/distribution/configs/distribution.conf" $'global.netplay=0\nglobal.netplay.relay=none\n'
            w "$d/storage/.config/retroarch/retroarch.cfg" $'netplay_nat_traversal = "true"\n'
            w "$d/etc/connman/main.conf" $'[General]\nNetworkInterfaceBlacklist = vmnet,vboxnet\n'
            ;;
        knulli-h700)
            w "$d/etc/os-release" $'NAME=Batocera.linux\nVERSION_ID=2024.11\nOS_NAME="knulli"\n'
            w "$d/usr/bin/knulli-settings-get" $'#!/bin/sh\n'
            w "$d/userdata/system/machine-id" "0a0b0c0d0e0f00010203040506070809"
            w "$d/userdata/system/knulli.conf" $'# KNULLI settings\nsystem.services=ssh samba\nglobal.netplay=1\nsystem.usbmode=adb\n'
            # a user's own boot hook, without a final newline
            w "$d/boot/boot-custom.sh" $'#!/bin/bash\n[ "$1" = "start" ] || exit 0\necho my own boot step'
            w "$d/etc/connman/main.conf" $'[General]\nNetworkInterfaceBlacklist=vmnet,p2p\n'
            with_udc "$d" 5100000.udc-controller
            mkdir -p "$d/sys/kernel/config/usb_gadget/g1/functions/ffs.adb"
            w "$d/sys/kernel/config/usb_gadget/g1/UDC" $'5100000.udc-controller\n'
            w "$d/usr/lib/libretro/nestopia_libretro.so" 'nestopia 1.54'
            w "$d/usr/lib/libretro/fceumm_libretro.so" 'fceumm knulli build'
            w "$d/usr/lib/libretro/genesisplusgx_libretro.so" 'genesis plus gx'
            w "$d/usr/share/emulationstation/es_systems.cfg" '<systemList>
  <system>
    <fullname>Nintendo Entertainment System</fullname>
    <name>nes</name>
    <emulators>
      <emulator name="libretro">
        <cores>
          <core default="true">fceumm</core>
          <core>nestopia</core>
        </cores>
      </emulator>
    </emulators>
  </system>
  <system>
    <name>snes</name>
    <emulators><emulator name="libretro"><cores><core default="true">snes9x</core></cores></emulator></emulators>
  </system>
</systemList>
'
            ;;
        batocera)
            w "$d/etc/os-release" $'NAME=Batocera.linux\nVERSION_ID=43\n'
            w "$d/usr/bin/batocera-settings-get" $'#!/bin/sh\n'
            w "$d/userdata/system/machine-id" "1111222233334444aaaabbbbccccdddd"
            w "$d/userdata/system/batocera.conf" $'system.services=\n'
            w "$d/etc/connman/main.conf" $'[General]\nNetworkInterfaceBlacklist=vmnet\n'
            with_udc "$d" fe800000.usb
            ;;
        darkos)
            w "$d/etc/os-release" $'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nNAME="Debian GNU/Linux"\n'
            mkdir -p "$d/home/ark"
            w "$d/etc/machine-id" "deadbeefdeadbeefdeadbeefdeadbeef"
            w "$d/home/ark/.config/retroarch/retroarch.cfg" $'netplay_nat_traversal = "true"\n'
            w "$d/home/ark/.config/retroarch32/retroarch.cfg" $'netplay_public_announce = "true"\n'
            w "$d/etc/NetworkManager/NetworkManager.conf" $'[main]\nplugins=ifupdown,keyfile\n\n[keyfile]\nunmanaged-devices=interface-name:p2p0;interface-name:ap0\n'
            ;;
        muos)
            w "$d/etc/os-release" $'NAME="muOS"\n'
            mkdir -p "$d/opt/muos/frontend"
            w "$d/opt/muos/share/info/config/retroarch.cfg" $'netplay_nat_traversal = "true"\n'
            ;;
        spruce)
            mkdir -p "$d/mnt/SDCARD/spruce"
            w "$d/etc/os-release" ''
            ;;
        unknown)
            w "$d/etc/os-release" $'NAME="Some Linux"\n'
            with_udc "$d" musb-hdrc.0
            ;;
        onion)
            mkdir -p "$d/mnt/SDCARD/.tmp_update"
            w "$d/etc/os-release" ''
            rm -rf "$d/sys/bus/usb/devices/usb1"
            ;;
        *) echo "unknown fixture $name" >&2; return 1 ;;
    esac
}

# usb_netdev DIR IFACE DRIVER [SERIAL] [VID:PID] [MAC]: a USB network
# interface from a device on the host port (net/IFACE -> device -> interface
# dir of a USB device). Default: a handheld gadget, 1d6b:0104, with a locally
# administered MAC address.
usb_netdev() {
    local d=$1 ifc=$2 drv=$3 serial=$4 id=${5:-1d6b:0104} mac=${6:-12:34:56:78:9a:bc}
    local dev="$d/sys/devices/platform/ehci/usb1/1-1"
    mkdir -p "$dev/1-1:1.0/net/$ifc" "$d/sys/bus/usb/drivers/$drv"
    [ -n "$serial" ] && w "$dev/serial" "$serial"$'\n'
    w "$dev/idVendor" "${id%:*}"$'\n'
    w "$dev/idProduct" "${id#*:}"$'\n'
    w "$dev/1-1:1.0/net/$ifc/address" "$mac"$'\n'
    ln -sfn "../../../../../../bus/usb/drivers/$drv" "$dev/1-1:1.0/driver"
    ln -sfn "../../devices/platform/ehci/usb1/1-1/1-1:1.0/net/$ifc" "$d/sys/class/net/$ifc"
    ln -sfn "../../../1-1:1.0" "$dev/1-1:1.0/net/$ifc/device"
}

# snapshot DIR: every file and symlink with its content hash (dirs ignored)
snapshot() {
    (cd "$1" && find . \( -type f -o -type l \) | LC_ALL=C sort | while read -r f; do
        if [ -L "$f" ]; then printf '%s -> %s\n' "$f" "$(readlink "$f")"; else printf '%s %s\n' "$f" "$(md5sum < "$f" | cut -c1-32)"; fi
    done)
}
