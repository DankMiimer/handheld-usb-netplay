# dArkOS and ArkOS (Ubuntu/Debian based, writable root, user ark).
# Untested (no device): built from dArkOS's sources. The RK3326 OTG port is
# host-only unless the device tree is changed, which USB Netplay never does.
# shellcheck shell=sh

UDEV_DIR=/etc/udev/rules.d
UNIT_DIR=/etc/systemd/system
NMCONF=/etc/NetworkManager/NetworkManager.conf

fw_service_install() { systemd_service_install; }
fw_service_remove() { systemd_service_remove; }

fw_netmgr() {
    local cur
    [ -f "$R$NMCONF" ] || return 0
    cur=$(get_key "$NMCONF" unmanaged-devices)
    case "$cur" in
        *interface-name:usb\**) return 0 ;;
    esac
    if [ -n "$cur" ]; then
        set_key "$NMCONF" unmanaged-devices "$cur;interface-name:usb*;interface-name:rndis*"
    else
        put_file /etc/NetworkManager/conf.d/90-usb-netplay.conf <<'EOF'
# USB Netplay: leave USB cable links between handhelds alone
[keyfile]
unmanaged-devices=interface-name:usb*;interface-name:rndis*
EOF
    fi
    NOTE_NETMGR="NetworkManager will leave USB links alone after the next restart"
}

fw_retroarch() {
    local f
    for f in /home/ark/.config/retroarch/retroarch.cfg /home/ark/.config/retroarch32/retroarch.cfg; do
        [ -f "$R$f" ] || continue
        retroarch_keys "$f"
        run chown ark:ark "$R$f"
    done
}

fw_gadget_setup() { :; }
fw_watch_tick() { [ "$DEV_MODE" = own ] && own_gadget_up; }
fw_hooks_extra() { :; }
fw_restart_frontend() { :; }
