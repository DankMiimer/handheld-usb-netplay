# ROCKNIX (and JELOS). Sourced by usbnp after detect_firmware.
# Tested on: RK3566 (Anbernic RG DS, 20261001) and RK3326 (R36S clone, AurkNix 20260622).
# shellcheck shell=sh

UDEV_DIR=/storage/.config/udev.rules.d
UNIT_DIR=/storage/.config/system.d
SYSCFG=/storage/.config/system/configs/system.cfg
RACFG=/storage/.config/retroarch/retroarch.cfg
NETCONF=/storage/.cache/usbgadget/network.conf

fw_service_install() { systemd_service_install; }
fw_service_remove() { systemd_service_remove; }

fw_netmgr() {
    if [ -e "$R/etc/NetworkManager/NetworkManager.conf" ]; then
        # ROCKNIX's NetworkManager config already leaves gadget, usb0, usbnet
        # and rndis0 alone; other names are handled with nmcli at link-up
        NOTE_NETMGR="NetworkManager already leaves USB links alone"
    else
        connman_blacklist /storage/.config/connman_main.conf
    fi
}

fw_retroarch() {
    local before
    before=$(cat "$R$SYSCFG" 2>/dev/null)
    set_key "$SYSCFG" global.netplay 1
    set_key "$SYSCFG" global.netplay_public_announce 0
    set_key "$SYSCFG" global.netplay.relay none
    [ "$before" = "$(cat "$R$SYSCFG" 2>/dev/null)" ] || RESTART_FRONTEND=1
    retroarch_keys "$RACFG"
}

# The device side: ROCKNIX's own USB network mode, with this handheld's subnet.
fw_gadget_setup() {
    [ "$DEV_MODE" = native ] || return 0
    [ "$(get_key "$NETCONF" IP)" = "$SUBNET.2" ] && [ "$(get_key "$NETCONF" NETMASK)" = 255.255.255.0 ] && return 0
    if [ ! -f "$R$NETCONF" ]; then
        # usbgadget writes the rest (the HOSTIP line) when it first runs
        mkdir -p "$(dirname "$R$NETCONF")"
    fi
    set_key "$NETCONF" IP "$SUBNET.2"
    set_key "$NETCONF" NETMASK 255.255.255.0
    # shellcheck disable=SC2016   # the line is usbgadget's own, unexpanded on purpose
    grep -q '^HOSTIP=' "$R$NETCONF" || printf '%s\n' 'HOSTIP=${IP%.*}.$((${IP##*.}-1))' >> "$R$NETCONF"
    rocknix_gadget_check force
}

# rocknix_gadget_check [force]: in USB network mode, keep the gadget
# interface up with its address and the DHCP server answering. ROCKNIX's boot
# can leave it down (usbgadget --start stops partway on some builds).
rocknix_gadget_check() {
    local udc ip pid
    udc=$(first_udc)
    [ -n "$udc" ] || return 0
    [ "$(cat "$R/sys/class/udc/$udc/function" 2>/dev/null)" = cdc ] || return 0
    [ -e "$R/sys/class/net/gadget" ] || return 0
    ip=$(get_key "$NETCONF" IP)
    [ -n "$ip" ] || return 0
    gadget_if_up gadget "$ip" "$(mask2prefix "$(get_key "$NETCONF" NETMASK)")"
    pid=$(cat "$R/var/run/udhcpd.pid" 2>/dev/null)
    if [ "$1" = force ] || [ -z "$pid" ] || [ ! -d "$R/proc/$pid" ] || ! grep -q "^start ${ip%.*}.1\$" "$R/var/run/udhcpd.conf" 2>/dev/null; then
        [ -n "$pid" ] && run kill "$pid" 2>/dev/null
        # the same server and settings usbgadget itself starts
        cat > "$R/var/run/udhcpd.conf" <<EOF
interface gadget
start ${ip%.*}.1
end ${ip%.*}.1
opt subnet $(get_key "$NETCONF" NETMASK)
opt lease 86400
max_leases 1
lease_file /dev/null
pidfile	/var/run/udhcpd.pid
remaining no
EOF
        have udhcpd && run udhcpd -S /var/run/udhcpd.conf
    fi
    gadget_peer_name "${ip%.*}.1"
}

fw_watch_tick() { rocknix_gadget_check; }

fw_hooks_extra() { :; }

fw_restart_frontend() {
    # ES keeps system.cfg in memory; restart it once so it reads netplay=1
    if run systemctl is-active -q essway.service 2>/dev/null; then
        run_bg sh -c 'sleep 3; systemctl restart essway.service'
    else
        run_bg sh -c 'sleep 3; systemctl restart emustation.service'
    fi
}
