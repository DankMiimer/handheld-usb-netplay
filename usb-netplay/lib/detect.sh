# Firmware and device identity. Sourced, not run.
# Sets FW, FW_LABEL, DEVICE, BASE (where the runtime is installed), IDFILE.
# shellcheck shell=sh

osr() {
    sed -n "s/^$1=//p" "$R/etc/os-release" 2>/dev/null | head -n 1 | tr -d '"'
}

detect_firmware() {
    FW=unknown
    if [ -d "$R/opt/muos" ]; then
        FW=muos
    elif [ -d "$R/mnt/SDCARD/spruce" ]; then
        FW=spruce
    elif [ -d "$R/mnt/SDCARD/.tmp_update" ]; then
        FW=onion
    elif [ -e "$R/usr/bin/knulli-settings-get" ] || [ "$(osr OS_NAME)" = knulli ]; then
        FW=knulli
    elif [ -e "$R/usr/bin/batocera-settings-get" ]; then
        FW=batocera
    else
        case "$(osr OS_NAME) $(osr NAME)" in
            *ROCKNIX*|*JELOS*) FW=rocknix ;;
            *AmberELEC*|*351ELEC*) FW=amberelec ;;
        esac
    fi
    if [ "$FW" = unknown ]; then
        if [ -d "$R/home/ark" ]; then
            FW=darkos       # dArkOS, and ArkOS which it replaced
        elif [ -d "$R/mnt/FunKey" ] || [ -e "$R/usr/local/sbin/adb" ]; then
            FW=funkey
        fi
    fi

    case "$FW" in
        rocknix)   FW_LABEL="ROCKNIX $(osr OS_VERSION)";  BASE=/storage/.config/usb-netplay; IDFILE=/storage/.cache/systemd-machine-id ;;
        amberelec) FW_LABEL="AmberELEC $(osr VERSION)";   BASE=/storage/.config/usb-netplay; IDFILE=/storage/.cache/systemd-machine-id ;;
        knulli)    FW_LABEL="KNULLI $(osr VERSION_ID)";   BASE=/userdata/system/usb-netplay; IDFILE=/userdata/system/machine-id ;;
        batocera)  FW_LABEL="Batocera $(osr VERSION_ID)"; BASE=/userdata/system/usb-netplay; IDFILE=/userdata/system/machine-id ;;
        darkos)    FW_LABEL="$(osr PRETTY_NAME) (ArkOS family)"; BASE=/opt/usb-netplay; IDFILE=/etc/machine-id ;;
        muos)      FW_LABEL="muOS";       BASE=${USBNP_HERE:-/opt/usb-netplay}; IDFILE=/etc/machine-id ;;
        *)         FW_LABEL="$FW";        BASE=/tmp/usb-netplay; IDFILE=/etc/machine-id ;;
    esac
    [ -e "$R$IDFILE" ] || IDFILE=/etc/machine-id
    FW_LABEL=$(printf '%s' "$FW_LABEL" | sed 's/[[:space:]]*$//')

    DEVICE=$(tr -d '\0' 2>/dev/null < "$R/sys/firmware/devicetree/base/model")
    [ -n "$DEVICE" ] || DEVICE=$(osr HW_DEVICE)
    [ -n "$DEVICE" ] || DEVICE=$(cat "$R/etc/hostname" 2>/dev/null)
    SOC=$(osr HW_DEVICE)
}

# device_id: a stable identifier for this handheld (never shown in full)
device_id() {
    local id
    id=$(tr -d ' \n' 2>/dev/null < "$R$IDFILE")
    [ -n "$id" ] || id=$(tr -d ' \n' 2>/dev/null < "$R/etc/machine-id")
    [ -n "$id" ] || id=$(cat "$R"/sys/class/net/wlan0/address 2>/dev/null)
    [ -n "$id" ] || id=$(cat "$R/etc/hostname" 2>/dev/null)
    printf '%s' "${id:-usb-netplay}"
}

# subnet_of_id: this handheld's own link subnet, 10.H1.H2.0/24 with H1 in
# 160..191, from a hash of its id. Used when it is the USB device: it is .2
# and the host on the other end is .1.
subnet_of_id() {
    local n
    n=$(hash32 "$1")
    printf '10.%d.%d' $((160 + n % 32)) $(((n / 32) % 256))
}
