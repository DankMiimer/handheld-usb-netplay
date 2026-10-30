# The cable link itself. Sourced, not run.
#
# Host side (this handheld has the OTG adapter): when a USB network interface
# from another handheld appears, find the other handheld's address and give
# this end a /32 address plus host routes. Never a default route or DNS, so
# Wi-Fi keeps working, and /32 so two links on one handheld never clash.
#
# Device side: this handheld's own subnet is 10.H1.H2.0/24 (see subnet_of_id);
# it is .2, the host is .1.
# shellcheck shell=sh

# Known static addresses of gadgets that don't hand out an address and don't
# carry one in their USB serial: "peer this-end" pairs.
STATIC_PEERS="10.170.0.5:10.170.0.6 169.254.170.2:169.254.170.1 10.1.1.2:10.1.1.1"
USB_NET_DRIVERS="cdc_ncm cdc_ether rndis_host cdc_eem cdc_subset"

ipcmd() { run ip "$@"; }
# ipq ARGS: read-only ip queries (nothing in a dry run)
ipq() { [ -n "$USBNP_DRYRUN" ] && return 0; ip "$@" 2>/dev/null; }

if_driver() { basename "$(readlink "$R/sys/class/net/$1/device/driver" 2>/dev/null)" 2>/dev/null; }

# usb_dev_dir IF: sysfs directory of the USB device behind a USB network interface
usb_dev_dir() {
    local d
    d=$(cd "$R/sys/class/net/$1/device" 2>/dev/null && pwd -P) || return 1
    dirname "$d"
}

is_usb_net() {
    local drv
    drv=$(if_driver "$1")
    case " $USB_NET_DRIVERS " in *" $drv "*) return 0 ;; esac
    return 1
}

mask2prefix() {
    local n b
    n=0
    for b in $(printf '%s' "$1" | tr '.' ' '); do
        case "$b" in
            255) n=$((n + 8)) ;; 254) n=$((n + 7)) ;; 252) n=$((n + 6)) ;; 248) n=$((n + 5)) ;;
            240) n=$((n + 4)) ;; 224) n=$((n + 3)) ;; 192) n=$((n + 2)) ;; 128) n=$((n + 1)) ;;
        esac
    done
    printf '%s' "$n"
}

ping_ok() {
    if [ -n "$USBNP_DRYRUN" ]; then
        # tests: everything answers, except with USBNP_PING_FAILS=1 the address .99
        [ -n "$USBNP_PING_FAILS" ] && [ "${1##*.}" = 99 ] && return 1
        return 0
    fi
    ping -c 1 -W 1 "$1" >/dev/null 2>&1
}

# find_peer IF: prints "PEER ME HOW" for the handheld at the other end
find_peer() {
    local dev serial out ip mask srv pair p me
    dev=$(usb_dev_dir "$1")
    serial=$(cat "$dev/serial" 2>/dev/null)
    # 1. a USB Netplay gadget carries its address in its serial number
    case "$serial" in
        usbnp-10.*.*.2)
            ip=${serial#usbnp-}
            printf '%s %s serial\n' "$ip" "${ip%.2}.1"
            return 0 ;;
    esac
    # 2. the gadget hands out an address (ROCKNIX's udhcpd): ask it
    ipcmd link set "$1" up
    if [ -n "$USBNP_FAKE_DHCP" ]; then
        out=$USBNP_FAKE_DHCP
    elif [ -n "$PY" ]; then
        out=$("$PY" "$HERE/lib/dhcp_probe.py" "$1" 6 2>/dev/null)
    fi
    if [ -n "$out" ]; then
        # shellcheck disable=SC2086   # "IP MASK SERVER" split into words on purpose
        set -- $out
        ip=$1 mask=$2 srv=$3
        [ -n "$srv" ] && { printf '%s %s dhcp\n' "$srv" "$ip"; return 0; }
    fi
    # 3. known static gadget addresses (RG Nano USB Link, ROCKNIX and JELOS defaults)
    for pair in $STATIC_PEERS; do
        p=${pair%%:*} me=${pair#*:}
        ipcmd addr add "$me/32" dev "$IFACE"
        ipcmd route replace "$p/32" dev "$IFACE" src "$me"
        if ping_ok "$p"; then
            printf '%s %s static\n' "$p" "$me"
            return 0
        fi
        ipcmd addr del "$me/32" dev "$IFACE"
    done
    return 1
}

# link_up IF: host side, called (detached) by the udev rule
link_up() {
    local peer me how
    IFACE=$1
    is_usb_net "$IFACE" || { log "link-up $IFACE: not a USB network interface ($(if_driver "$IFACE"))"; return 0; }
    have nmcli && run nmcli device set "$IFACE" managed no >/dev/null 2>&1
    # shellcheck disable=SC2046   # "PEER ME HOW" split into words on purpose
    set -- $(find_peer "$IFACE")
    peer=$1 me=$2 how=$3
    if [ -z "$peer" ]; then
        log "link-up $IFACE: nothing answered on the other end"
        return 1
    fi
    ipcmd link set "$IFACE" up
    ipcmd addr replace "$me/32" dev "$IFACE"
    ipcmd route replace "$peer/32" dev "$IFACE" src "$me"
    # RetroArch's LAN host list searches with 255.255.255.255: send it over the cable
    ipcmd route replace 255.255.255.255/32 dev "$IFACE"
    mkdir -p "$STATE/links"
    printf 'peer=%s\nme=%s\nhow=%s\nside=host\n' "$peer" "$me" "$how" > "$STATE/links/$IFACE"
    hosts_set "$NETPLAY_NAME" "$peer"
    log "link-up $IFACE: peer $peer ($how), this end $me"
}

# link_down IF: the interface went away
link_down() {
    local f gone newest
    f="$STATE/links/$1"
    [ -f "$f" ] || return 0
    gone=$(sed -n 's/^peer=//p' "$f")
    rm -f "$f"
    log "link-down $1: peer $gone"
    [ "$(hosts_get "$NETPLAY_NAME")" = "$gone" ] || return 0
    newest=$(ls -t "$STATE/links" 2>/dev/null | head -n 1)
    if [ -n "$newest" ]; then
        hosts_set "$NETPLAY_NAME" "$(sed -n 's/^peer=//p' "$STATE/links/$newest")"
    else
        hosts_set "$NETPLAY_NAME" "$FALLBACK_HOST"
    fi
}

# --- device side -----------------------------------------------------------

gadget_if_up() {
    local ifc ip prefix cur
    ifc=$1 ip=$2 prefix=$3
    [ -e "$R/sys/class/net/$ifc" ] || return 1
    cur=$(ipq -4 -o addr show dev "$ifc")
    case "$cur" in
        *" $ip/$prefix "*) : ;;
        *) ipcmd -f inet addr flush dev "$ifc"; ipcmd addr add "$ip/$prefix" dev "$ifc"; log "gadget $ifc: address $ip/$prefix put back" ;;
    esac
    ipq link show dev "$ifc" | grep -q '[<,]UP[,>]' || ipcmd link set "$ifc" up
    return 0
}

# own_gadget_up: build and bind USB Netplay's own network gadget (DEV_MODE=own)
own_gadget_up() {
    local g udc fn id mac
    g="$R$GADGETS/usbnp"
    udc=$(first_udc)
    [ -n "$udc" ] || return 1
    [ -n "$OWN_FUNC" ] || return 1
    if [ ! -d "$g" ]; then
        id=$(hash32 "$(device_id)")
        mac=$(printf '%02x:%02x:%02x:%02x' $(((id >> 24) & 255)) $(((id >> 16) & 255)) $(((id >> 8) & 255)) $((id & 255)))
        mkdir -p "$g/strings/0x409" "$g/configs/c.1/strings/0x409" "$g/functions/$OWN_FUNC.usb0" || return 1
        echo 0x1d6b > "$g/idVendor"
        echo 0x0104 > "$g/idProduct"
        echo 0x0200 > "$g/bcdUSB"
        echo "USB Netplay" > "$g/strings/0x409/manufacturer"
        printf '%s\n' "${DEVICE:-handheld}" > "$g/strings/0x409/product"
        echo "usbnp-$SUBNET.2" > "$g/strings/0x409/serialnumber"
        echo "USB Netplay" > "$g/configs/c.1/strings/0x409/configuration"
        echo "02:$mac:02" > "$g/functions/$OWN_FUNC.usb0/dev_addr" 2>/dev/null
        echo "12:$mac:01" > "$g/functions/$OWN_FUNC.usb0/host_addr" 2>/dev/null
        ln -s "$g/functions/$OWN_FUNC.usb0" "$g/configs/c.1/" 2>/dev/null
    fi
    [ -n "$(cat "$g/UDC" 2>/dev/null)" ] || echo "$udc" > "$g/UDC" || return 1
    fn=$(cat "$g/functions/$OWN_FUNC.usb0/ifname" 2>/dev/null)
    [ -n "$fn" ] || return 0
    gadget_if_up "$fn" "$SUBNET.2" 24
    gadget_peer_name "$SUBNET.1" "$fn"
}

own_gadget_down() {
    local g
    g="$R$GADGETS/usbnp"
    [ -d "$g" ] || return 0
    echo "" > "$g/UDC" 2>/dev/null
    rm -f "$g"/configs/c.1/*.usb0 2>/dev/null
    rmdir "$g"/configs/c.1/strings/0x409 "$g"/configs/c.1 "$g"/functions/* "$g"/strings/0x409 "$g" 2>/dev/null
}
