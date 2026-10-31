# The Game Boy link cable over the USB cable: Gambatte's network Game Link.
# Sourced, not run.
#
# Gambatte, the GB/GBC core, can link two Game Boys over TCP: one side is the
# server, the other connects to it by address. USB Netplay picks the roles from
# the cable: the USB host is the server, the USB device is the client. The
# device always knows the host's address: .1 of its own subnet, or 127.0.0.1
# through the ADB tunnel. RetroArch reads core options when a game starts, so
# start the game after the cable link is up. GBLINK=no in settings.conf leaves
# Gambatte's settings alone. Firmwares opt in with GBLINK_FW=1 and
# fw_gambatte_set KEY VALUE [KEY VALUE...] (ROCKNIX, KNULLI, Batocera).
# shellcheck shell=sh

GBLINK_PORT=56400

# gblink_role: "server", "client ADDRESS" or "off", from the links right now
gblink_role() {
    local f
    for f in "$STATE"/links/*; do
        [ -f "$f" ] && { echo server; return 0; }
    done
    # the ADB tunnel is serving a handheld with USB Netplay on the host port
    [ -s "$STATE/adb-device-info" ] && { echo server; return 0; }
    for f in "$STATE"/gadget-host.*; do
        # only a handheld running USB Netplay (it answered the info request), not a PC
        [ -f "$f" ] && [ -s "$STATE/peer-info.${f##*/gadget-host.}" ] && { echo "client $(head -n 1 "$f")"; return 0; }
    done
    [ -f "$STATE/adb-up" ] && { echo "client 127.0.0.1"; return 0; }
    echo off
}

# gblink_digits ADDRESS: the address as Gambatte's 12 digits (010170000006)
gblink_digits() {
    local IFS
    IFS=.
    # shellcheck disable=SC2086   # split the address at the dots
    set -- $1
    printf '%03d%03d%03d%03d' "$1" "$2" "$3" "$4"
}

# gblink_update: set Gambatte's link mode for the links right now (only on a
# change; a handheld that never had a cable link keeps its own settings). A
# link that drops for a moment keeps its role: KNULLI's ADB drops for a few
# seconds when a game starts, just when the game reads its settings. Only 30 s
# without a link (USBNP_GBLINK_OFF_DELAY) turns the Game Boy link off.
gblink_update() {
    local want have mode addr digits n now since delay mine
    [ "$GBLINK" = no ] && return 0
    [ -n "$GBLINK_FW" ] || return 0
    want=$(gblink_role)
    have=$(cat "$STATE/gblink" 2>/dev/null)
    if [ "$want" = "$have" ]; then
        rm -f "$STATE/gblink-off"
        return 0
    fi
    [ "$want" = off ] && [ -z "$have" ] && return 0
    delay=${USBNP_GBLINK_OFF_DELAY:-30}
    if [ "$want" = off ] && [ "$delay" -gt 0 ]; then
        now=$(date +%s)
        since=$(cat "$STATE/gblink-off" 2>/dev/null)
        [ -n "$since" ] || { echo "$now" > "$STATE/gblink-off"; return 0; }
        [ $((now - since)) -ge "$delay" ] || return 0
    fi
    rm -f "$STATE/gblink-off"
    addr=
    case "$want" in
        server) mode="Network Server" ;;
        client*) mode="Network Client" addr=${want#client } ;;
        *) mode="Not Connected" ;;
    esac
    set -- gambatte_gb_link_mode "$mode" gambatte_gb_link_network_port "$GBLINK_PORT"
    if [ -n "$addr" ]; then
        digits=$(gblink_digits "$addr")
        n=1
        while [ $n -le 12 ]; do
            set -- "$@" "gambatte_gb_link_network_server_ip_$n" "$(printf '%s' "$digits" | cut -c$n)"
            n=$((n + 1))
        done
    fi
    # one writer at a time, all keys in one write
    if [ -z "$CONF_LOCKED" ]; then
        conf_lock || return 0
        mine=1
    fi
    fw_gambatte_set "$@"
    [ -n "$mine" ] && conf_unlock
    mkdir -p "$STATE"
    echo "$want" > "$STATE/gblink"
    log "gblink: Gambatte link mode $mode${addr:+, server $addr}"
}

# gblink_report: the Game Boy link lines of the status
gblink_report() {
    local have
    [ -n "$GBLINK_FW" ] || return 0
    if [ "$GBLINK" = no ]; then
        echo "Game Boy link cable (GB/GBC, Gambatte): left alone (GBLINK=no)"
        return 0
    fi
    have=$(cat "$STATE/gblink" 2>/dev/null)
    case "$have" in
        server)
            echo "Game Boy link cable (GB/GBC, Gambatte): this handheld is the link server."
            echo "  Start the game here first, then on the other handheld. Wait about"
            echo "  30 s, then start the link here (2 PLAYER, the Cable Club...)." ;;
        client*)
            echo "Game Boy link cable (GB/GBC, Gambatte): this handheld connects to ${have#client }."
            echo "  Start the game on the other handheld first, then here, and start"
            echo "  the link on the other handheld." ;;
        *)
            echo "Game Boy link cable (GB/GBC, Gambatte): set up when a cable link is up" ;;
    esac
}
