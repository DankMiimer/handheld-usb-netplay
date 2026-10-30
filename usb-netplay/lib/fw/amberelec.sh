# AmberELEC. Sourced by usbnp after detect_firmware.
# Untested (no device): built from AmberELEC's sources. Its kernels have no USB
# network gadget, so it is the USB host only.
# shellcheck shell=sh

UDEV_DIR=/storage/.config/udev.rules.d
UNIT_DIR=/storage/.config/system.d
DISTCFG=/storage/.config/distribution/configs/distribution.conf
RACFG=/storage/.config/retroarch/retroarch.cfg

fw_service_install() { systemd_service_install; }
fw_service_remove() { systemd_service_remove; }
fw_netmgr() { connman_blacklist /storage/.config/connman_main.conf; }

fw_retroarch() {
    local before
    before=$(cat "$R$DISTCFG" 2>/dev/null)
    set_key "$DISTCFG" global.netplay 1
    set_key "$DISTCFG" global.netplay_public_announce 0
    # AmberELEC turns the relay ON for any value but empty or 0 (even "none")
    set_key "$DISTCFG" global.netplay.relay 0
    [ "$before" = "$(cat "$R$DISTCFG" 2>/dev/null)" ] || RESTART_FRONTEND=1
    retroarch_keys "$RACFG"
}

fw_gadget_setup() { :; }
fw_watch_tick() { :; }
fw_hooks_extra() { :; }

fw_restart_frontend() {
    run_bg sh -c 'sleep 3; systemctl restart emustation.service'
}
