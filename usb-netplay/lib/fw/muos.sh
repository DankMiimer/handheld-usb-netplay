# muOS. Untested (no device): built from muOS's sources (2606 "Andromeda").
# muOS 2606+ links two muOS handhelds by itself (Device Link, interface
# usblink0, 169.254.x addresses). RetroArch's LAN host list ignores 169.254, so
# USB Netplay adds a 10.250.x address on the same link, mapped from the one
# Device Link chose, and points usb-netplay at the other muOS handheld.
# shellcheck shell=sh

UDEV_DIR=/etc/udev/rules.d
RACFG=/opt/muos/share/info/config/retroarch.cfg
LINKSTATE=/run/muos/network/link

fw_service_install() {
    # muOS runs user scripts at boot only with Advanced > User Init enabled
    if [ -d "$R/run/muos/storage/init" ]; then
        put_file /run/muos/storage/init/usb-netplay.sh 755 <<EOF
#!/bin/sh
# USB Netplay's background loop (installed by USB Netplay)
setsid /bin/sh $BASE/usbnp watch </dev/null >/dev/null 2>&1 &
EOF
        NOTE_SERVICE="starts at boot through muOS User Init (Settings > Advanced > User Init must be on)"
    else
        NOTE_SERVICE="muOS User Init is off, so open USB Netplay once after each restart"
    fi
    run_bg /bin/sh "$R$BASE/usbnp" watch
}

fw_service_remove() { run pkill -f "$BASE/usbnp watch"; run pkill -f "$BASE/lib/info.py"; }
fw_netmgr() { :; }   # muOS only runs DHCP on wlan0

fw_retroarch() { [ -f "$R$RACFG" ] && retroarch_keys "$RACFG"; }
fw_gadget_setup() { :; }
fw_hooks_extra() { :; }
fw_restart_frontend() { :; }

fw_watch_tick() {
    local a peer mine last
    [ "$DEV_MODE" = own ] && own_gadget_up
    [ -e "$R/sys/class/net/usblink0" ] && [ -f "$R$LINKSTATE" ] || return 0
    a=$(sed -n 's/^address=//p' "$R$LINKSTATE")
    peer=$(sed -n 's/^peer_address=//p' "$R$LINKSTATE")
    case "$a" in 169.254.*) : ;; *) return 0 ;; esac
    # 169.254.X.Y -> 10.250.X.Y on the same link
    mine="10.250.${a#169.254.}"; mine=${mine%/*}
    last="10.250.${peer#169.254.}"; last=${last%/*}
    gadget_if_extra usblink0 "$mine" 24
    [ -n "${peer}" ] && hosts_set "$NETPLAY_NAME" "$last"
}

gadget_if_extra() {
    ipq -4 -o addr show dev "$1" | grep -q " $2/$3 " || ipcmd addr add "$2/$3" dev "$1"
}
