# What this handheld tells the one at the other end of the cable, and the
# comparison of the two in the status. Sourced, not run.
#
# Netplay needs the same core build on both ends, and "Connect to Netplay
# Host" keeps the core it started with, so different default cores fail with
# "host is running another core". Both showed up in real use; the status
# points them out before a join.
# shellcheck shell=sh

INFO_CORES="nestopia fceumm snes9x gambatte mgba gpsp genesis_plus_gx beetle_pce_fast"
INFO_SYSTEMS="nes snes gb gbc gba megadrive genesis pcengine"

# core_canon NAME: one name per core (KNULLI's genesisplusgx, pce_fast)
core_canon() {
    case "$1" in
        genesisplusgx) echo genesis_plus_gx ;;
        pce_fast) echo beetle_pce_fast ;;
        *) echo "$1" ;;
    esac
}

# core_dir: where this firmware's RetroArch cores are
core_dir() {
    local d
    for d in /tmp/cores /usr/lib/libretro /home/ark/.config/retroarch/cores /opt/muos/share/core; do
        [ -d "$R$d" ] && { echo "$d"; return 0; }
    done
    return 1
}

# core_md5s: "NAME MD5" (8 hex digits) for the netplay cores that are here
core_md5s() {
    local d c f
    d=$(core_dir) || return 0
    for c in $INFO_CORES genesisplusgx pce_fast; do
        f="$R$d/${c}_libretro.so"
        [ -f "$f" ] && printf '%s %s\n' "$(core_canon "$c")" "$(md5sum < "$f" | cut -c1-8)"
    done
}

# default_core SYSTEM: the core the frontend starts SYSTEM's games with
default_core() {
    local v f
    case "$FW" in
        rocknix)
            v=$(get_key /storage/.config/system/configs/system.cfg "$1.core")
            f=/storage/.config/emulationstation/es_systems.cfg ;;
        knulli|batocera)
            v=$(get_key "$SETCONF" "$1.core")
            f=/usr/share/emulationstation/es_systems.cfg ;;
        *) return 0 ;;
    esac
    [ -n "$v" ] && { core_canon "$v"; return 0; }
    [ -f "$R$f" ] || return 0
    v=$(awk -v s="$1" '
        /<system>/ { n = ""; d = "" }
        /<name>/ && n == "" { n = $0; gsub(/.*<name>|<\/name>.*/, "", n) }
        /<core default="true">/ && n == s && d == "" { d = $0; gsub(/.*<core default="true">|<\/core>.*/, "", d) }
        /<\/system>/ && n == s && d != "" { print d; exit }' "$R$f")
    [ -n "$v" ] && core_canon "$v"
    return 0
}

# info_write: this handheld's info, served to the other end of the cable
info_write() {
    local c s
    mkdir -p "$STATE"
    {
        echo "usbnp=$USBNP_VERSION"
        echo "firmware=$FW_LABEL"
        echo "device=${DEVICE:-a handheld}"
        core_md5s | while read -r c s; do echo "core.$c=$s"; done
        for s in $INFO_SYSTEMS; do
            c=$(default_core "$s")
            [ -z "$c" ] || echo "default.$s=$c"
        done
    } > "$STATE/info.tmp"
    mv "$STATE/info.tmp" "$STATE/info"
}

# peer_info ADDRESS: the other handheld's info, or nothing
peer_info() {
    if [ -n "$USBNP_FAKE_PEER_INFO" ]; then
        cat "$USBNP_FAKE_PEER_INFO" 2>/dev/null
        return 0
    fi
    [ -n "$PY" ] && [ -z "$USBNP_DRYRUN" ] && "$PY" "$HERE/lib/info.py" ask "$1" 1 2>/dev/null
}

info_field() { printf '%s\n' "$1" | sed -n "s/^$(key_regex "$2")=//p" | head -n 1; }

# peer_report ADDRESS: compare this handheld with the one at ADDRESS
peer_report() {
    local theirs mine c a b same diff only s
    theirs=$(peer_info "$1")
    if [ -z "$theirs" ]; then
        echo "  ($1 doesn't answer USB Netplay's info request: it has no USB Netplay 0.1.3 or newer, or isn't a handheld)"
        return 0
    fi
    info_write
    mine=$(cat "$STATE/info")
    echo "  It is $(info_field "$theirs" device), $(info_field "$theirs" firmware), USB Netplay $(info_field "$theirs" usbnp)"
    same='' diff='' only=''
    for c in $INFO_CORES; do
        a=$(info_field "$mine" "core.$c") b=$(info_field "$theirs" "core.$c")
        if [ -n "$a" ] && [ "$a" = "$b" ]; then
            same="$same $c"
        elif [ -n "$a" ] && [ -n "$b" ]; then
            diff="$diff $c"
        elif [ -n "$a$b" ]; then
            only="$only $c"
        fi
    done
    [ -n "$same" ] && echo "  Same core builds on both:$same"
    [ -n "$diff" ] && echo "  DIFFERENT builds (netplay may refuse or desync):$diff"
    [ -n "$only" ] && echo "  On one handheld only:$only"
    for s in $INFO_SYSTEMS; do
        a=$(info_field "$mine" "default.$s") b=$(info_field "$theirs" "default.$s")
        [ -n "$a" ] && [ -n "$b" ] && [ "$a" != "$b" ] &&
            echo "  $s games start with $a here, $b there: \"Connect to Netplay Host\" fails with \"host is running another core\" (use the host list, or set the same default core)"
    done
    return 0
}
