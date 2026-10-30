# What this handheld can be on the cable. Sourced, not run.
# Sets after caps_probe:
#   DEV_MODE  how it can be the USB *device*:
#             native   the firmware's own network gadget (ROCKNIX usbgadget)
#             own      a network gadget built here (kernel has NCM/ECM/RNDIS)
#             adb      only the firmware's ADB gadget; the host on the other
#                      end runs the ADB tunnel (needs python3 on both)
#             devlink  muOS Device Link (muOS to muOS only)
#             none     can't be the USB device
#   DEV_WHY   why, in one line
#   HOST_OK   1 if it can be the USB host (OTG adapter on this end)
#   OWN_FUNC  ncm|ecm|rndis, the function an own gadget would use
#   PY        python3 path or empty
# shellcheck shell=sh

GADGETS=/sys/kernel/config/usb_gadget

first_udc() { ls "$R/sys/class/udc" 2>/dev/null | head -n 1; }

# gadget_bound: name of the configfs gadget holding the UDC, if any
gadget_bound() {
    local g u
    for g in "$R$GADGETS"/*; do
        [ -f "$g/UDC" ] || continue
        u=$(cat "$g/UDC" 2>/dev/null)
        [ -n "$u" ] && { basename "$g"; return 0; }
    done
    return 1
}

# adb_gadget: name of a firmware gadget with a FunctionFS ADB function, if any
adb_gadget() {
    local g
    for g in "$R$GADGETS"/*; do
        [ -d "$g/functions/ffs.adb" ] && { basename "$g"; return 0; }
    done
    return 1
}

# probe_functions: which network gadget functions this kernel can make,
# by creating them in a scratch gadget (removed again right away)
probe_functions() {
    local t f out
    out=
    [ -d "$R$GADGETS" ] || { [ -z "$R" ] && have modprobe && modprobe libcomposite 2>/dev/null; }
    [ -d "$R$GADGETS" ] || return 0
    t="$R$GADGETS/usbnp_probe"
    mkdir "$t" 2>/dev/null || return 0
    mkdir -p "$t/functions" 2>/dev/null   # configfs makes it by itself; a test tree doesn't
    for f in ncm ecm rndis; do
        [ -z "$R" ] && have modprobe && modprobe "usb_f_$f" 2>/dev/null
        if mkdir "$t/functions/$f.p" 2>/dev/null; then
            out="$out $f"
            rmdir "$t/functions/$f.p" 2>/dev/null
        fi
    done
    rmdir "$t/functions" 2>/dev/null
    rmdir "$t" 2>/dev/null
    printf '%s\n' "$out" | sed 's/^ //'
}

caps_probe() {
    PY=$(command -v python3 2>/dev/null)
    HOST_OK=0
    ls "$R/sys/bus/usb/devices" 2>/dev/null | grep -q '^usb[0-9]' && HOST_OK=1
    OWN_FUNC=
    DEV_MODE=none
    DEV_WHY=

    case "$FW" in
        rocknix)
            case "$SOC" in
                RK3399) DEV_WHY="ROCKNIX's RK3399 kernel has no USB network gadget or NCM host driver"; HOST_OK=0; return ;;
            esac
            if [ -e "$R/usr/bin/usbgadget" ]; then
                DEV_MODE=native; DEV_WHY="ROCKNIX USB network mode (usbgadget)"
            fi
            return ;;
        amberelec)
            DEV_WHY="AmberELEC kernels have no USB network gadget; this handheld can only be the USB host"
            return ;;
        onion)
            HOST_OK=0; DEV_WHY="the Miyoo Mini's USB-C port carries no USB data"
            return ;;
        spruce)
            HOST_OK=0; DEV_WHY="SpruceOS has no USB network gadget and no boot hook for USB Netplay"
            return ;;
        unknown)
            HOST_OK=0; DEV_WHY="USB Netplay doesn't know this firmware"
            return ;;
        funkey)
            DEV_WHY="on the RG Nano use the USB Link app (rg-nano-adb-tools); it is a network gadget the other handheld can host"
            HOST_OK=0
            return ;;
    esac

    if [ "$FW" = muos ] && { [ -e "$R/opt/muos/frontend/mulink" ] || [ -e "$R/run/muos/network/link" ]; }; then
        DEV_MODE=devlink; DEV_WHY="muOS Device Link (works with another muOS handheld)"
    fi

    if [ -z "$(first_udc)" ]; then
        [ "$DEV_MODE" = none ] && DEV_WHY="no USB device controller (on ArkOS/dArkOS RK3326 the OTG port is host-only unless the device tree is changed)"
        return
    fi

    # a firmware ADB gadget (KNULLI's USB mode "adb") keeps the controller;
    # don't take it over, the other end tunnels through ADB instead
    if adb_gadget >/dev/null; then
        if [ -n "$PY" ]; then
            DEV_MODE=adb; DEV_WHY="firmware ADB mode; the USB host on the other end tunnels netplay through ADB"
        else
            DEV_WHY="firmware ADB mode, but no python3 for the ADB tunnel helper"
        fi
        return
    fi

    # any other firmware gadget (file transfer, mass storage) holds the
    # controller too: leave it, and say how to free it
    local holder
    holder=$(gadget_bound)
    if [ -n "$holder" ] && [ "$holder" != usbnp ]; then
        [ "$DEV_MODE" = none ] && DEV_WHY="the firmware's own USB mode ($holder) is using the USB port; set the USB mode to off to use cable netplay"
        return
    fi

    OWN_FUNC=$(probe_functions | cut -d' ' -f1)
    if [ -n "$OWN_FUNC" ]; then
        [ "$DEV_MODE" = devlink ] || { DEV_MODE=own; DEV_WHY="USB network gadget built by USB Netplay ($OWN_FUNC)"; }
    elif [ "$DEV_MODE" = none ]; then
        DEV_WHY="this kernel can't make a USB network gadget"
    fi
}
