# KNULLI and Batocera (Batocera reuses this file with its own names).
# Tested on: KNULLI on H700 (Anbernic RG34XXSP). Batocera and other KNULLI
# SoCs: built from their sources, untested.
# shellcheck shell=sh

UDEV_DIR=/userdata/system/udev/rules.d
SERVICES=/userdata/system/services
if [ "$FW" = batocera ]; then
    SETCONF=/userdata/system/batocera.conf
else
    SETCONF=/userdata/system/knulli.conf
fi

fw_service_install() {
    local svc
    svc="$SERVICES/usb_netplay"
    put_file "$svc" 755 <<EOF
#!/bin/bash
# USB Netplay's background loop (installed by USB Netplay). Started at boot by
# S99userservices because usb_netplay is listed in system.services.
PID=/var/run/usb-netplay.pid
case "\$1" in
  start)
    [ -f "\$PID" ] && kill -0 "\$(cat "\$PID")" 2>/dev/null && exit 0
    setsid /bin/sh $BASE/usbnp watch </dev/null >/dev/null 2>&1 &
    echo \$! > "\$PID"
    ;;
  stop)
    [ -f "\$PID" ] && kill "\$(cat "\$PID")" 2>/dev/null
    rm -f "\$PID"
    pkill -f "$BASE/adb/tunnel.py" 2>/dev/null
    pkill -f "$BASE/lib/info.py" 2>/dev/null
    ;;
esac
exit 0
EOF
    local cur
    cur=$(get_key "$SETCONF" system.services)
    case " $cur " in
        *" usb_netplay "*) : ;;
        *) set_key "$SETCONF" system.services "$(printf '%s' "$cur usb_netplay" | sed 's/^ //')" ;;
    esac
    run /bin/bash "$R$svc" stop
    run /bin/bash "$R$svc" start
}

fw_service_remove() {
    [ -f "$R$SERVICES/usb_netplay" ] && run /bin/bash "$R$SERVICES/usb_netplay" stop
}

fw_netmgr() {
    # connman runs with SingleConnectedTechnology: a USB link it manages drops
    # Wi-Fi and later loses its address. Blacklist USB network interfaces
    # early in every boot, before connman starts.
    add_block /boot/boot-custom.sh <<'EOF'
[ "$1" = "start" ] && for p in usb rndis; do
    grep -q "^NetworkInterfaceBlacklist=.*\b$p\b" /etc/connman/main.conf || sed -i "s/^NetworkInterfaceBlacklist=/&$p,/" /etc/connman/main.conf
done
EOF
    NOTE_NETMGR="connman will leave USB links alone after the next restart"
    NEEDS_REBOOT=1
}

fw_retroarch() {
    set_key "$SETCONF" global.netplay 1
    set_key "$SETCONF" global.netplay_public_announce 0
    set_key "$SETCONF" global.netplay.relay none
    # global.retroarch.KEY=VALUE is passed straight into retroarch.cfg at every launch
    set_key "$SETCONF" global.retroarch.netplay_nat_traversal false
    set_key "$SETCONF" global.retroarch.netplay_public_announce false
    set_key "$SETCONF" global.retroarch.netplay_use_mitm_server false
    set_key "$SETCONF" global.retroarch.netplay_ip_address "$NETPLAY_NAME"
}

fw_gadget_setup() { :; }

fw_watch_tick() {
    [ "$DEV_MODE" = own ] && own_gadget_up
}

# KNULLI binds its ADB gadget only at boot; at every unplug adbd restarts its
# USB side and the kernel unbinds the gadget, so put it back on each change.
fw_hooks_extra() {
    [ "$DEV_MODE" = adb ] || return 0
    put_file "$UDEV_DIR/91-usb-netplay-adb.rules" <<EOF
# USB Netplay: rebind the firmware's ADB gadget after an unplug (installed by USB Netplay)
ACTION=="change", SUBSYSTEM=="udc", RUN+="/bin/sh -c 'setsid /bin/sh $BASE/usbnp rebind-adb </dev/null >/dev/null 2>&1 &'"
EOF
}

fw_restart_frontend() { :; }
