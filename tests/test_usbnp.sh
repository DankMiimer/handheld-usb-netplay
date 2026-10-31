#!/bin/bash
# usbnp against fake firmware trees: detection, capabilities, setup and
# remove (which must leave every tree exactly as it was), host-side link-up
# by serial / DHCP / static address, and the watch loop. usbnp itself runs
# under dash (a strict POSIX shell), like busybox ash on the handhelds.
cd "$(dirname "$0")" || exit 1
TESTS=$PWD
USBNP=$(cd ../usb-netplay && pwd)/usbnp
SH=${USBNP_TEST_SHELL:-dash}
# the Game Boy link turns off at once in the tests (one check sets 30 s)
export USBNP_GBLINK_OFF_DELAY=0
# shellcheck source=fixtures.sh
. ./fixtures.sh

PASS=0 FAIL=0
check() {
    if [ "$2" = "$3" ]; then PASS=$((PASS + 1)); else
        FAIL=$((FAIL + 1)); printf 'FAIL %s\n  expected: %s\n  got:      %s\n' "$1" "$2" "$3"; fi
}
has() {
    case "$3" in *"$2"*) PASS=$((PASS + 1)) ;; *)
        FAIL=$((FAIL + 1)); printf 'FAIL %s\n  wanted:   %s\n  in:       %s\n' "$1" "$2" "$(printf '%s' "$3" | head -c 600)" ;; esac
}
hasnt() {
    case "$3" in *"$2"*) FAIL=$((FAIL + 1)); printf 'FAIL %s\n  did not want: %s\n' "$1" "$2" ;; *) PASS=$((PASS + 1)) ;; esac
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
ROOT=$WORK/root
DRY=$WORK/dry.log

u() {   # u ARGS: run usbnp in the fake tree, dry run
    : > "$DRY"
    USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY $SH "$USBNP" "$@"
}
field() { printf '%s\n' "$2" | sed -n "s/^$1=//p"; }

echo "== detection and capabilities"
while read -r name fw base mode host; do
    make_root "$name" "$ROOT"
    out=$(u detect)
    check "$name: firmware" "$fw" "$(field FW "$out")"
    check "$name: install dir" "$base" "$(field BASE "$out")"
    check "$name: device mode" "$mode" "$(field DEV_MODE "$out")"
    check "$name: host mode" "$host" "$(field HOST_OK "$out")"
done <<'EOF'
rocknix-nm      rocknix   /storage/.config/usb-netplay  native 1
rocknix-connman rocknix   /storage/.config/usb-netplay  native 1
rocknix-rk3399  rocknix   /storage/.config/usb-netplay  none   0
amberelec       amberelec /storage/.config/usb-netplay  none   1
knulli-h700     knulli    /userdata/system/usb-netplay  adb    1
batocera        batocera  /userdata/system/usb-netplay  own    1
darkos          darkos    /opt/usb-netplay              none   1
onion           onion     /tmp/usb-netplay              none   0
spruce          spruce    /tmp/usb-netplay              none   0
unknown         unknown   /tmp/usb-netplay              none   0
EOF
make_root batocera "$ROOT"
check "batocera: own gadget function" ncm "$(field OWN_FUNC "$(u detect)")"
check "the scratch probe gadget is removed again" "" "$(ls "$ROOT/sys/kernel/config/usb_gadget")"
make_root knulli-h700 "$ROOT"
echo "mtp" > /dev/null
rm -rf "$ROOT/sys/kernel/config/usb_gadget/g1/functions/ffs.adb"
mkdir -p "$ROOT/sys/kernel/config/usb_gadget/g1/functions/mass_storage.0"
out=$(u detect)
check "a firmware gadget in file-transfer mode is left alone" none "$(field DEV_MODE "$out")"

echo "== subnet from the device id"
make_root rocknix-nm "$ROOT"
s1=$(field SUBNET "$(u detect)")
s2=$(field SUBNET "$(u detect)")
check "same id, same subnet" "$s1" "$s2"
case "$s1" in 10.1[6-9][0-9].*|10.16[0-9].*) PASS=$((PASS + 1)) ;; *) FAIL=$((FAIL + 1)); echo "FAIL subnet out of range: $s1" ;; esac
make_root knulli-h700 "$ROOT"
s3=$(field SUBNET "$(u detect)")
[ "$s1" != "$s3" ] && PASS=$((PASS + 1)) || { FAIL=$((FAIL + 1)); echo "FAIL two ids gave the same subnet $s1"; }

echo "== setup and remove leave every tree exactly as it was"
for name in rocknix-nm rocknix-connman amberelec knulli-h700 batocera darkos muos; do
    make_root "$name" "$ROOT"
    before=$(snapshot "$ROOT")
    out=$(u setup 2>&1)
    has "$name: status after setup" "USB Netplay" "$out"
    after_setup=$(snapshot "$ROOT")
    [ "$before" != "$after_setup" ] && PASS=$((PASS + 1)) || { FAIL=$((FAIL + 1)); echo "FAIL $name: setup changed nothing"; }
    u remove >/dev/null 2>&1
    check "$name: remove restores the tree" "$before" "$(snapshot "$ROOT")"
done

echo "== what setup changes, per firmware"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
dry=$(cat "$DRY")
sys=$(cat "$ROOT/storage/.config/system/configs/system.cfg")
ra=$(cat "$ROOT/storage/.config/retroarch/retroarch.cfg")
has "rocknix: netplay on" "global.netplay=1" "$sys"
hasnt "rocknix: the old value is gone" "global.netplay=0" "$sys"
has "rocknix: not announced" "global.netplay_public_announce=0" "$sys"
has "rocknix: pre-filled host" 'netplay_ip_address = "usb-netplay"' "$ra"
has "rocknix: NAT traversal off" 'netplay_nat_traversal = "false"' "$ra"
has "rocknix: other keys untouched" 'video_driver = "gl"' "$ra"
rules=$(cat "$ROOT/storage/.config/udev.rules.d/90-usb-netplay.rules")
has "rocknix: udev rule pokes the installed copy" 'RUN+="/bin/sh /storage/.config/usb-netplay/usbnp poke"' "$rules"
hasnt "rocknix: no slow work inside udev (it kills leftovers)" "link-up" "$rules"
unit=$(cat "$ROOT/storage/.config/system.d/usb-netplay.service")
has "rocknix: unit runs watch" "ExecStart=/bin/sh /storage/.config/usb-netplay/usbnp watch" "$unit"
check "rocknix: unit has no ordering (the black-screen loop)" 0 "$(grep -c -E '^(After|Before|Requires)=' "$ROOT/storage/.config/system.d/usb-netplay.service")"
check "rocknix: unit enabled" "../usb-netplay.service" "$(readlink "$ROOT/storage/.config/system.d/multi-user.target.wants/usb-netplay.service")"
has "rocknix: service started" "run: systemctl restart usb-netplay.service" "$dry"
net=$(cat "$ROOT/storage/.cache/usbgadget/network.conf")
has "rocknix: gadget gets its own subnet" "IP=$s1.2" "$net"
has "rocknix: /24" "NETMASK=255.255.255.0" "$net"
has "rocknix: the HOSTIP line is kept" 'HOSTIP=${IP%.*}' "$net"
[ -x "$ROOT/storage/.config/usb-netplay/usbnp" ] && PASS=$((PASS + 1)) || { FAIL=$((FAIL + 1)); echo "FAIL runtime not installed"; }
has "rocknix: frontend restart for system.cfg" "systemctl restart" "$dry"

make_root rocknix-connman "$ROOT"
u setup >/dev/null 2>&1
cm=$(cat "$ROOT/storage/.config/connman_main.conf")
has "rocknix connman: USB blacklisted" "NetworkInterfaceBlacklist=usb,gadget,rndis,vmnet" "$cm"
has "rocknix connman: rest of main.conf copied" "PreferredTechnologies = ethernet" "$cm"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
kc=$(cat "$ROOT/userdata/system/knulli.conf")
has "knulli: service listed" "system.services=ssh samba usb_netplay" "$kc"
has "knulli: pre-filled host" "global.retroarch.netplay_ip_address=usb-netplay" "$kc"
has "knulli: relay off" "global.netplay.relay=none" "$kc"
has "knulli: other keys untouched" "system.usbmode=adb" "$kc"
has "knulli: ADB rebind rule" 'SUBSYSTEM=="udc"' "$(cat "$ROOT/userdata/system/udev/rules.d/91-usb-netplay-adb.rules")"
bc=$(cat "$ROOT/boot/boot-custom.sh")
has "knulli: connman blacklist at boot" 'NetworkInterfaceBlacklist=' "$bc"
has "knulli: the user's boot step kept" "echo my own boot step" "$bc"
has "knulli: service script runs watch" "/userdata/system/usb-netplay/usbnp watch" "$(cat "$ROOT/userdata/system/services/usb_netplay")"
chmod a-w "$ROOT/boot/boot-custom.sh" "$ROOT/boot"   # KNULLI mounts /boot read-only
again=$(u setup 2>&1)
chmod u+w "$ROOT/boot" "$ROOT/boot/boot-custom.sh"
hasnt "knulli: setup again doesn't write /boot when the block is unchanged" "boot-custom" "$again"
check "knulli: setup twice adds one block" 1 "$(grep -c '^# >>> usb-netplay' "$ROOT/boot/boot-custom.sh")"
check "knulli: setup twice lists the service once" "system.services=ssh samba usb_netplay" "$(grep '^system.services=' "$ROOT/userdata/system/knulli.conf")"

make_root darkos "$ROOT"
u setup >/dev/null 2>&1
has "darkos: NetworkManager leaves usb* alone" "unmanaged-devices=interface-name:p2p0;interface-name:ap0;interface-name:usb*;interface-name:rndis*" "$(cat "$ROOT/etc/NetworkManager/NetworkManager.conf")"
has "darkos: both RetroArch configs" 'netplay_ip_address = "usb-netplay"' "$(cat "$ROOT/home/ark/.config/retroarch32/retroarch.cfg")"
has "darkos: chown to ark" "run: chown ark:ark" "$(cat "$DRY")"

make_root amberelec "$ROOT"
u setup >/dev/null 2>&1
has "amberelec: relay off is 0 there" "global.netplay.relay=0" "$(cat "$ROOT/storage/.config/distribution/configs/distribution.conf")"

make_root onion "$ROOT"
before=$(snapshot "$ROOT")
out=$(u setup 2>&1)
has "onion: says why" "no USB data" "$out"
check "onion: nothing changed" "$before" "$(snapshot "$ROOT")"
for name in spruce unknown; do
    make_root "$name" "$ROOT"
    before=$(snapshot "$ROOT")
    has "$name: says it isn't supported" "Not supported here" "$(u setup 2>&1)"
    check "$name: nothing changed" "$before" "$(snapshot "$ROOT")"
done

echo "== host side: finding the other handheld"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm "usbnp-10.170.5.2"
u link-up usb0
dry=$(cat "$DRY")
has "serial: this end /32" "run: ip addr replace 10.170.5.1/32 dev usb0" "$dry"
has "serial: host route to the peer" "run: ip route replace 10.170.5.2/32 dev usb0 src 10.170.5.1" "$dry"
has "serial: LAN search goes over the cable" "run: ip route replace 255.255.255.255/32 dev usb0" "$dry"
hasnt "never a default route" "default" "$dry"
check "serial: usb-netplay points at the peer" $'10.170.5.2\tusb-netplay' "$(grep usb-netplay "$ROOT/run/rocknix/hosts")"
check "hosts symlink kept (ROCKNIX)" /run/rocknix/hosts "$(readlink "$ROOT/etc/hosts")"
has "serial: a notice in the menu" "run: notify USB Netplay: cable link up, the other end is 10.170.5.2" "$dry"
has "status lists the link" "usb0: the other handheld is 10.170.5.2 (serial)" "$(u status)"
u link-down usb0
check "link-down clears usb-netplay" "" "$(grep usb-netplay "$ROOT/run/rocknix/hosts")"
has "link-down: a notice in the menu" "run: notify USB Netplay: cable link down" "$(cat "$DRY")"
w "$ROOT/sys/class/net/gadget/carrier" $'1\n'
has "status names the host on this handheld's gadget" "gadget (this handheld is the USB device): the other handheld is $s1.1" "$(u status)"
rm -rf "$ROOT/sys/class/net/gadget"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm "ffeeddccbbaa99887766554433221100"
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_DHCP="10.171.9.1 255.255.255.0 10.171.9.2 -" $SH "$USBNP" link-up usb0
has "dhcp: this end from the offer" "run: ip addr replace 10.171.9.1/32 dev usb0" "$(cat "$DRY")"
check "dhcp: usb-netplay points at the DHCP server" $'10.171.9.2\tusb-netplay' "$(grep usb-netplay "$ROOT/etc/hosts")"
u link-down usb0

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm ""
printf 'FALLBACK_HOST=192.0.2.40\n' > "$ROOT/userdata/system/usb-netplay/settings.conf"
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY PATH=/nonexistent:/usr/bin:/bin $SH "$USBNP" link-up usb0 </dev/null
has "static: the RG Nano's USB Link address is tried first" "run: ip route replace 10.170.0.5/32 dev usb0 src 10.170.0.6" "$(cat "$DRY")"
check "static: usb-netplay points at it" $'10.170.0.5\tusb-netplay' "$(grep usb-netplay "$ROOT/etc/hosts")"
u link-down usb0
check "unplugged: usb-netplay falls back to FALLBACK_HOST" $'192.0.2.40\tusb-netplay' "$(grep usb-netplay "$ROOT/etc/hosts")"

make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm "12c0000000000000" 8087:011e
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_DHCP="10.171.9.1 255.255.255.0 10.171.9.2 -" $SH "$USBNP" link-up usb0
check "RG Nano: static address first, no DHCP probe" "peer=10.170.0.5 how=static" "$(sed -n 's/^\(peer\|how\)=/&/p' "$ROOT/storage/.config/usb-netplay/state/links/usb0" | tr '\n' ' ' | sed 's/ $//')"

make_root rocknix-nm "$ROOT"
mkdir -p "$ROOT/sys/class/net/wlan0"
u link-up wlan0
check "Wi-Fi is never touched" "" "$(cat "$DRY")"

echo "== host side: phones and network adapters are left alone"
PHONE_DHCP="192.168.42.47 255.255.255.0 192.168.42.129 192.168.42.129"
make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm "" 18d1:4eeb
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_DHCP=$PHONE_DHCP $SH "$USBNP" link-up usb0
dry=$(cat "$DRY")
hasnt "phone: no address set" "addr replace" "$dry"
hasnt "phone: no static addresses tried" "addr add" "$dry"
has "phone: renamed, so connman takes it again (USB Netplay hid usb*)" "run: ip link set usb0 name tether0" "$dry"
check "phone: usb-netplay untouched" "" "$(grep usb-netplay "$ROOT/etc/hosts")"
has "phone: status says so" "tether0: not a handheld (a network with gateway 192.168.42.129" "$(u status)"

make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 rndis_host "" 18d1:4ee3
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_DHCP=$PHONE_DHCP $SH "$USBNP" link-up usb0
dry=$(cat "$DRY")
hasnt "phone on a NetworkManager build: not renamed (ROCKNIX's own config decides)" "name tether" "$dry"
hasnt "phone on a NetworkManager build: no address set" "addr replace" "$dry"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" eth1 cdc_ncm "" 0bda:8156 00:e0:4c:68:01:02
u link-up eth1
check "USB Ethernet adapter (maker MAC): not touched at all" "" "$(grep 'run: ip' "$DRY")"
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
hasnt "watch: an adapter that was left alone isn't probed again" "eth1" "$(cat "$DRY")"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm "" 04e8:6863
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY PATH=/nonexistent:/usr/bin:/bin $SH "$USBNP" link-up usb0 </dev/null
hasnt "unknown device without a DHCP answer: no static addresses tried" "addr add" "$(cat "$DRY")"

echo "== watch: a link missed by udev (boot) and a missed unplug"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb1 cdc_ether "usbnp-10.188.3.2"
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch
check "watch: sets up a link it finds" $'10.188.3.2\tusb-netplay' "$(grep usb-netplay "$ROOT/run/rocknix/hosts")"
rm -f "$ROOT/sys/class/net/usb1"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch
check "watch: drops a link whose interface is gone" "" "$(grep usb-netplay "$ROOT/run/rocknix/hosts")"
usb_netdev "$ROOT" usb1 cdc_ether "usbnp-10.188.3.2"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch
sed -i 's/^peer=.*/peer=10.188.3.99/' "$ROOT/storage/.config/usb-netplay/state/links/usb1"
for n in 1 2 3; do
    USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 USBNP_PING_FAILS=1 $SH "$USBNP" watch
done
check "watch: re-finds a peer that stopped answering" "peer=10.188.3.2" "$(grep peer= "$ROOT/storage/.config/usb-netplay/state/links/usb1")"

echo "== watch: a handheld that answers late (RG Nano USB Link) is tried again after 10 s"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
usb_netdev "$ROOT" usb0 cdc_ncm ""
LINKS=$ROOT/storage/.config/usb-netplay/state/links
watch_once() { USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 USBNP_PING_FAILS=$1 $SH "$USBNP" watch </dev/null; }
watch_once all
check "late: nothing answered at first" "" "$(ls "$LINKS")"
watch_once ""
check "late: not tried again after 5 s" "" "$(ls "$LINKS")"
watch_once ""
check "late: tried again after 10 s" "peer=10.170.0.5" "$(grep peer= "$LINKS/usb0" 2>/dev/null)"
for n in 1 2 3 4 5 6; do watch_once ""; done
check "late: a linked interface isn't retried" 0 "$(ls "$ROOT/storage/.config/usb-netplay/state" | grep -c '^tried\.')"

echo "== watch: a host port stuck after a failed connection gets its controller reset"
# stuck_port DIR DRIVER: an EHCI-like controller whose port 1 is in "default" with no device
stuck_port() {
    local hc="$1/sys/devices/platform/fd800000.usb"
    mkdir -p "$hc/usb3/3-0:1.0/usb3-port1" "$1/sys/bus/platform/drivers/$2"
    w "$hc/usb3/3-0:1.0/usb3-port1/state" $'default\n'
    ln -sfn "../../../bus/platform/drivers/$2" "$hc/driver"
    ln -sfn "../../../devices/platform/fd800000.usb/usb3" "$1/sys/bus/usb/devices/usb3"
}
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
stuck_port "$ROOT" ehci-platform
: > "$DRY"
for n in 1 2; do USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null; done
hasnt "stuck port: not reset after 10 s" "unbind" "$(cat "$DRY")"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "stuck port: controller reset after 15 s" "fd800000.usb > $ROOT/sys/bus/platform/drivers/ehci-platform/unbind" "$(cat "$DRY")"
: > "$DRY"
for n in 1 2 3; do USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null; done
hasnt "stuck port: at most once in 2 minutes" "unbind" "$(cat "$DRY")"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
stuck_port "$ROOT" dwc2
: > "$DRY"
for n in 1 2 3 4; do USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null; done
hasnt "stuck port: a dual-role controller (it may carry the gadget) is never reset" "unbind" "$(cat "$DRY")"

echo "== hosts-adb (the ADB helper)"
make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
u hosts-adb up
check "tunnel up: usb-netplay is 127.0.0.1" $'127.0.0.1\tusb-netplay' "$(grep usb-netplay "$ROOT/etc/hosts")"
has "tunnel up: a notice in the menu" "run: notify USB Netplay: cable link up (through ADB)" "$(cat "$DRY")"
u hosts-adb up
check "tunnel up again (the helper's 10 s refresh): no second notice" 0 "$(grep -c notify "$DRY")"
printf 'NOTIFY=no\n' > "$ROOT/userdata/system/usb-netplay/settings.conf"
u hosts-adb down
check "tunnel down: usb-netplay cleared" "" "$(grep usb-netplay "$ROOT/etc/hosts")"
check "NOTIFY=no: no notice" 0 "$(grep -c notify "$DRY")"

echo "== info for the other handheld, and the comparison in the status"
make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
kinfo=$(u info)
has "info: firmware" "firmware=KNULLI 2024.11" "$kinfo"
has "info: core checksum" "core.nestopia=$(printf 'nestopia 1.54' | md5sum | cut -c1-8)" "$kinfo"
has "info: KNULLI's genesisplusgx under its common name" "core.genesis_plus_gx=" "$kinfo"
has "info: default core from es_systems.cfg" "default.nes=fceumm" "$kinfo"
has "info: default core of another system" "default.snes=snes9x" "$kinfo"
cp "$ROOT/userdata/system/usb-netplay/state/info" "$WORK/knulli-info"
printf 'nes.core=nestopia\n' >> "$ROOT/userdata/system/knulli.conf"
has "info: the user's core choice wins" "default.nes=nestopia" "$(u info)"

make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
has "info: ROCKNIX default core" "default.nes=nestopia" "$(u info)"
usb_netdev "$ROOT" usb0 cdc_ncm "usbnp-10.170.5.2"
u link-up usb0
st=$(USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_PEER_INFO=$WORK/knulli-info $SH "$USBNP" status)
has "compare: names the other handheld" "It is " "$st"
has "compare: same build" "Same core builds on both: nestopia" "$st"
has "compare: different build" "DIFFERENT builds (netplay may refuse or desync): fceumm" "$st"
has "compare: default core mismatch" "nes games start with nestopia here, fceumm there" "$st"
st=$(USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_FAKE_PEER_INFO=$WORK/nothing $SH "$USBNP" status)
has "compare: an old USB Netplay or no handheld" "doesn't answer USB Netplay's info request" "$st"
u link-down usb0
cp "$WORK/knulli-info" "$ROOT/storage/.config/usb-netplay/state/adb-device-info"
st=$(u status)
has "compare over ADB (host side): the tunnel's handheld" "ADB tunnel: linked to the handheld on the host port" "$st"
has "compare over ADB (host side): different build" "DIFFERENT builds (netplay may refuse or desync): fceumm" "$st"
sed 's/$/\r/' "$WORK/knulli-info" > "$ROOT/storage/.config/usb-netplay/state/adb-device-info"
st=$(u status)
has "compare: \\r\\n line ends (an old adbd's shell) still compare" "Same core builds on both: nestopia" "$st"
hasnt "compare: \\r\\n line ends make no false default-core mismatch" "snes games start" "$st"

echo "== Game Boy link cable (Gambatte), roles from the cable"
make_root rocknix-nm "$ROOT"
before=$(snapshot "$ROOT" | grep -v '^\./sys/')
u setup >/dev/null 2>&1
GBO=$ROOT/storage/.config/retroarch/config/Gambatte
[ ! -e "$GBO/gb.opt" ] && PASS=$((PASS + 1)) || { FAIL=$((FAIL + 1)); echo "FAIL gblink: setup without a link touched Gambatte"; }
usb_netdev "$ROOT" usb0 cdc_ncm "usbnp-10.170.5.2"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "gblink: the USB host is the server (gb)" 'gambatte_gb_link_mode = "Network Server"' "$(cat "$GBO/gb.opt" 2>/dev/null)"
has "gblink: the USB host is the server (gbc)" 'gambatte_gb_link_mode = "Network Server"' "$(cat "$GBO/gbc.opt" 2>/dev/null)"
has "gblink: port" 'gambatte_gb_link_network_port = "56400"' "$(cat "$GBO/gb.opt" 2>/dev/null)"
has "gblink: ROCKNIX's own Gambatte setting kept in the folder file" 'gambatte_gbc_color_correction = "disabled"' "$(cat "$GBO/gb.opt" 2>/dev/null)"
has "gblink: status explains the start order" "this handheld is the link server" "$(u status)"
rm -f "$ROOT/sys/class/net/usb0"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "gblink: unplugged, back to Not Connected" 'gambatte_gb_link_mode = "Not Connected"' "$(cat "$GBO/gb.opt" 2>/dev/null)"
u remove >/dev/null 2>&1
check "gblink: remove restores the tree (apart from the fake USB device)" "$before" "$(snapshot "$ROOT" | grep -v '^\./sys/')"

make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
s1=$(field SUBNET "$(u detect)")
mkdir -p "$ROOT/sys/class/net/gadget" "$ROOT/var/run"
w "$ROOT/sys/class/udc/fcc00000.usb/function" $'cdc\n'
w "$ROOT/sys/class/net/gadget/carrier" $'1\n'
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 USBNP_FAKE_PEER_INFO=$WORK/nothing $SH "$USBNP" watch </dev/null
[ ! -e "$GBO/gb.opt" ] && PASS=$((PASS + 1)) || { FAIL=$((FAIL + 1)); echo "FAIL gblink: a PC on the gadget (no USB Netplay info) made this a client"; }
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 USBNP_FAKE_PEER_INFO=$WORK/knulli-info $SH "$USBNP" watch </dev/null
gb=$(cat "$GBO/gb.opt" 2>/dev/null)
has "gblink: the USB device of a USB Netplay handheld is the client" 'gambatte_gb_link_mode = "Network Client"' "$gb"
digits=$(printf '%03d%03d%03d%03d' $(echo "$s1.1" | tr . ' '))
check "gblink: the client's server address is the host (.1)" "$digits" "$(for n in 1 2 3 4 5 6 7 8 9 10 11 12; do printf '%s' "$gb" | sed -n "s/^gambatte_gb_link_network_server_ip_$n = \"\(.\)\"/\1/p"; done | tr -d '\n')"

make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
cp "$WORK/knulli-info" "$ROOT/storage/.config/usb-netplay/state/adb-device-info"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "gblink: the ADB tunnel's host is the server" 'gambatte_gb_link_mode = "Network Server"' "$(cat "$GBO/gb.opt" 2>/dev/null)"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
u hosts-adb up
kc=$(cat "$ROOT/userdata/system/knulli.conf")
has "gblink KNULLI: through ADB the client connects to 127.0.0.1" "global.retroarchcore.gambatte_gb_link_mode=Network Client" "$kc"
has "gblink KNULLI: address digits" "global.retroarchcore.gambatte_gb_link_network_server_ip_3=7" "$kc"
has "gblink KNULLI: last digit" "global.retroarchcore.gambatte_gb_link_network_server_ip_12=1" "$kc"
u hosts-adb down
has "gblink KNULLI: tunnel down, Not Connected" "global.retroarchcore.gambatte_gb_link_mode=Not Connected" "$(cat "$ROOT/userdata/system/knulli.conf")"

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
u hosts-adb up
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_GBLINK_OFF_DELAY=30 $SH "$USBNP" hosts-adb down
has "gblink KNULLI: a short ADB drop (a game starting) keeps the client role" \
    "global.retroarchcore.gambatte_gb_link_mode=Network Client" "$(cat "$ROOT/userdata/system/knulli.conf")"

echo "== settings files survive USB Netplay processes running at once"
make_root knulli-h700 "$ROOT"
orig=$(cat "$ROOT/userdata/system/knulli.conf")
u setup >/dev/null 2>&1 &
for n in 1 2 3 4; do
    USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY.$n $SH "$USBNP" hosts-adb up &
    USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY.$n $SH "$USBNP" hosts-adb down &
done
wait
kc=$(cat "$ROOT/userdata/system/knulli.conf")
printf '%s\n' "$orig" | while IFS= read -r line; do
    case "$line" in system.services=*) continue ;; esac   # setup adds usb_netplay there
    printf '%s\n' "$kc" | grep -q -x -F -e "$line" || echo "LOST: $line"
done > "$WORK/lost"
check "parallel setup + ADB helper calls: no line of knulli.conf lost" "" "$(cat "$WORK/lost")"
has "parallel: setup's own change is there" "system.services=ssh samba usb_netplay" "$kc"
check "parallel: no temporary files left" "" "$(ls "$ROOT/userdata/system" | grep usbnp)"
rm -f "$DRY".?

make_root knulli-h700 "$ROOT"
u setup >/dev/null 2>&1
printf 'GBLINK=no\n' > "$ROOT/userdata/system/usb-netplay/settings.conf"
u hosts-adb up
hasnt "gblink: GBLINK=no leaves Gambatte alone" "gambatte_gb_link" "$(cat "$ROOT/userdata/system/knulli.conf")"
has "gblink: GBLINK=no in the status" "left alone (GBLINK=no)" "$(u status)"

echo "== device side: notices when a host starts and stops answering on the gadget"
make_root rocknix-nm "$ROOT"
u setup >/dev/null 2>&1
mkdir -p "$ROOT/sys/class/net/gadget" "$ROOT/var/run"
w "$ROOT/sys/class/udc/fcc00000.usb/function" $'cdc\n'
w "$ROOT/sys/class/net/gadget/carrier" $'1\n'
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "gadget: a notice when a host answers" "run: notify USB Netplay: cable link up, the other end is $s1.1" "$(cat "$DRY")"
: > "$DRY"
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
check "gadget: no repeat while it stays up" 0 "$(grep -c notify "$DRY")"
w "$ROOT/sys/class/net/gadget/carrier" $'0\n'
USBNP_ROOT=$ROOT USBNP_DRYRUN=$DRY USBNP_WATCH_ONCE=1 $SH "$USBNP" watch </dev/null
has "gadget: a notice when it goes" "run: notify USB Netplay: cable link down" "$(cat "$DRY")"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
