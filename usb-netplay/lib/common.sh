# Shared helpers for usbnp. Sourced, not run.
#
# Every path goes through $R, a root prefix that is empty on a real handheld,
# so the tests can run setup and remove against fake firmware trees. Commands
# that change the running system go through run(), which only records them
# when USBNP_DRYRUN names a file (the tests).
# shellcheck shell=sh

R=${USBNP_ROOT:-}

log() {
    [ -n "$LOG" ] || return 0
    printf '%s %s\n' "$(date '+%F %T' 2>/dev/null)" "$*" >> "$LOG" 2>/dev/null
    return 0
}

have() { command -v "$1" >/dev/null 2>&1; }

# hash32 STRING: a stable 32-bit number from STRING (md5sum: ROCKNIX's busybox has no cksum)
hash32() {
    local h
    h=$(printf '%s' "$1" | md5sum | cut -c1-8)
    printf '%d' "0x$h"
}

# run CMD ARGS...: run it, or in a dry run append it to $USBNP_DRYRUN
run() {
    if [ -n "$USBNP_DRYRUN" ]; then
        printf 'run:' >> "$USBNP_DRYRUN"
        printf ' %s' "$@" >> "$USBNP_DRYRUN"
        printf '\n' >> "$USBNP_DRYRUN"
        return 0
    fi
    "$@"
}

# run_bg CMD ARGS...: start it detached (udev must not wait for it)
run_bg() {
    if [ -n "$USBNP_DRYRUN" ]; then
        run "$@"
        return 0
    fi
    if have setsid; then
        setsid "$@" </dev/null >/dev/null 2>&1 &
    else
        "$@" </dev/null >/dev/null 2>&1 &
    fi
}

# resolve PATH: where a (possibly symlinked) path under $R really is, still under $R
resolve() {
    local p t n
    p="$R$1"
    n=0
    while [ -L "$p" ] && [ $n -lt 8 ]; do
        t=$(readlink "$p")
        case "$t" in
            /*) p="$R$t" ;;
            *) p="$(dirname "$p")/$t" ;;
        esac
        n=$((n + 1))
    done
    printf '%s\n' "$p"
}

# --- the change manifest -----------------------------------------------------
# setup records every change in $STATE/manifest so remove can undo it exactly.
# One change per line, fields separated by a tab:
#   file  PATH  saved|absent                 a whole file created or replaced
#   key   PATH  KEY  STYLE  saved|absent  OLD  one key in a settings file
#   block PATH                               a marked block added to a script
# PATH is the path on the handheld (without $R).

TAB=$(printf '\t')

manifest_has() {
    [ -f "$STATE/manifest" ] && grep -q -F -x -e "$1" "$STATE/manifest" 2>/dev/null
}

manifest_add() {
    mkdir -p "$STATE"
    printf '%s\n' "$1" >> "$STATE/manifest"
}

manifest_prefix_has() {
    [ -f "$STATE/manifest" ] && grep -q -F -e "$1" "$STATE/manifest" 2>/dev/null
}

save_name() { printf '%s' "$1" | sed 's|/|%|g'; }

# bk_file PATH: remember PATH as it is now (once), before we write it
bk_file() {
    manifest_prefix_has "file${TAB}$1${TAB}" && return 0
    if [ -e "$R$1" ] || [ -L "$R$1" ]; then
        mkdir -p "$STATE/backup"
        cp -p "$R$1" "$STATE/backup/$(save_name "$1")" 2>/dev/null || cp "$R$1" "$STATE/backup/$(save_name "$1")"
        manifest_add "file${TAB}$1${TAB}saved"
    else
        manifest_add "file${TAB}$1${TAB}absent"
    fi
}

# put_file PATH MODE: write stdin to PATH (backed up first)
put_file() {
    bk_file "$1"
    mkdir -p "$(dirname "$R$1")"
    cat > "$R$1.usbnp.tmp" && chmod "${2:-644}" "$R$1.usbnp.tmp" && mv -f "$R$1.usbnp.tmp" "$R$1"
}

key_regex() { printf '%s' "$1" | sed 's/[].[\\*^$/]/\\&/g'; }

# key_line KEY VALUE STYLE: the line as the file format wants it
key_line() {
    case "$3" in
        ra) printf '%s = "%s"' "$1" "$2" ;;   # retroarch.cfg
        *) printf '%s=%s' "$1" "$2" ;;        # KEY=VALUE files (system.cfg, knulli.conf, NetworkManager.conf)
    esac
}

# get_key PATH KEY: the current value, or nothing
get_key() {
    [ -f "$R$1" ] || return 0
    sed -n "s/^$(key_regex "$2")[[:space:]]*=[[:space:]]*//p" "$R$1" | head -n 1 | sed 's/^"\(.*\)"$/\1/'
}

# set_key PATH KEY VALUE STYLE: set one key, remembering its old line (once)
set_key() {
    local f k v st old new re
    f=$1 k=$2 v=$3 st=${4:-conf}
    re=$(key_regex "$k")
    new=$(key_line "$k" "$v" "$st")
    if ! manifest_prefix_has "key${TAB}$f${TAB}$k${TAB}"; then
        old=$(grep -m 1 "^${re}[[:space:]]*=" "$R$f" 2>/dev/null)
        if [ -n "$old" ]; then
            manifest_add "key${TAB}$f${TAB}$k${TAB}$st${TAB}saved${TAB}$old"
        else
            manifest_add "key${TAB}$f${TAB}$k${TAB}$st${TAB}absent${TAB}"
        fi
    fi
    [ -f "$R$f" ] || { mkdir -p "$(dirname "$R$f")"; : > "$R$f"; }
    replace_key_line "$R$f" "$re" "$new"
}

# set_keys PATH STYLE KEY VALUE [KEY VALUE...]: several keys in one write
set_keys() {
    local f st old re
    f=$1 st=$2
    shift 2
    [ -f "$R$f" ] || { mkdir -p "$(dirname "$R$f")"; : > "$R$f"; }
    while [ $# -ge 2 ]; do
        re=$(key_regex "$1")
        if ! manifest_prefix_has "key${TAB}$f${TAB}$1${TAB}"; then
            old=$(grep -m 1 "^${re}[[:space:]]*=" "$R$f" 2>/dev/null)
            if [ -n "$old" ]; then
                manifest_add "key${TAB}$f${TAB}$1${TAB}$st${TAB}saved${TAB}$old"
            else
                manifest_add "key${TAB}$f${TAB}$1${TAB}$st${TAB}absent${TAB}"
            fi
        fi
        printf '%s\t%s\n' "$re" "$(key_line "$1" "$2" "$st")"
        shift 2
    done | replace_key_lines "$R$f"
}

# conf_lock / conf_unlock: one USB Netplay process at a time changes settings
# files (setup, remove, the watch loop, the ADB helper's calls). Two writers
# at once once emptied a KNULLI knulli.conf.
conf_lock() {
    local l p n
    [ -n "$CONF_LOCKED" ] && return 0
    mkdir -p "$STATE"
    l="$STATE/lock.conf"
    n=0
    while ! mkdir "$l" 2>/dev/null; do
        p=$(cat "$l/pid" 2>/dev/null)
        if [ -n "$p" ] && ! kill -0 "$p" 2>/dev/null; then
            rm -rf "$l"   # held by a process that's gone
            continue
        fi
        n=$((n + 1))
        [ $n -ge 100 ] && { log "conf_lock: still held by $p after 20 s"; return 1; }
        sleep 0.2 2>/dev/null || sleep 1
    done
    echo $$ > "$l/pid"
    CONF_LOCKED=1
}
conf_unlock() {
    [ -n "$CONF_LOCKED" ] || return 0
    rm -rf "$STATE/lock.conf"
    CONF_LOCKED=
}

# write_over FILE TMP: put TMP in FILE's place in one step (rename), so no
# reader ever sees half a file; TMP was made with FILE's mode. Refuses to
# empty FILE or to shrink it below half its lines: USB Netplay never removes
# that much, so it would mean FILE was being written by someone else.
write_over() {
    local a b
    a=$(wc -l < "$1" 2>/dev/null || echo 0)
    b=$(wc -l < "$2" 2>/dev/null || echo 0)
    if { [ -s "$1" ] && [ ! -s "$2" ]; } || { [ "$a" -gt 10 ] && [ $((b * 2)) -lt "$a" ]; }; then
        rm -f "$2"
        log "write_over: refused to shrink $1 from $a to $b lines"
        return 1
    fi
    if [ -L "$1" ]; then
        cat "$2" > "$1" && rm -f "$2"   # keep the link
    else
        mv -f "$2" "$1"
    fi
}

# replace_key_lines FILE: apply "KEYREGEX<TAB>NEWLINE" pairs from stdin to
# FILE in one pass. Per key: the first match replaced, other matches dropped,
# appended when missing; NEWLINE empty means delete the key
replace_key_lines() {
    local tmp
    tmp="$1.usbnp.$$"
    cp -p "$1" "$tmp" 2>/dev/null || : > "$tmp"
    # the pairs through a file, not awk -v: awk would turn the \. in a pattern
    # (and any backslash in a value) into something else
    cat > "$tmp.keys"
    USBNP_KEYS="$tmp.keys" awk '
        BEGIN {
            FS = "\t"
            while ((getline line < ENVIRON["USBNP_KEYS"]) > 0) {
                n++; i = index(line, "\t")
                re[n] = "^" substr(line, 1, i - 1) "[[:space:]]*="; nl[n] = substr(line, i + 1)
            }
            FS = " "
        }
        {
            for (k = 1; k <= n; k++) if ($0 ~ re[k]) {
                if (!done[k] && nl[k] != "") print nl[k]
                done[k] = 1; next
            }
            print
        }
        END { for (k = 1; k <= n; k++) if (!done[k] && nl[k] != "") print nl[k] }
    ' "$1" > "$tmp" || { rm -f "$tmp" "$tmp.keys"; return 1; }
    rm -f "$tmp.keys"
    write_over "$1" "$tmp"
}

# replace_key_line FILE KEYREGEX NEWLINE: one key (see replace_key_lines)
replace_key_line() {
    printf '%s\t%s\n' "$2" "$3" | replace_key_lines "$1"
}

BLOCK_BEGIN='# >>> usb-netplay (added by USB Netplay; remove it with "USB Netplay - Remove")'
BLOCK_END='# <<< usb-netplay'

# rw_for PATH: KNULLI and Batocera mount /boot read-only; make it writable for
# a change there (rw_done makes it read-only again)
rw_for() {
    case "$1" in /boot/*) : ;; *) return 0 ;; esac
    [ -z "$R" ] || return 0   # a test tree is writable
    grep -q ' /boot [^ ]* ro[ ,]' /proc/mounts 2>/dev/null || return 0
    mount -o remount,rw /boot || return 1
    RW_BOOT=1
}
rw_done() {
    [ -n "$RW_BOOT" ] && mount -o remount,ro /boot
    RW_BOOT=
    return 0
}

# block_body FILE: what is between our markers in FILE now
block_body() {
    awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '$0 == b { on = 1; next } $0 == e { on = 0 } on' "$1"
}

# add_block PATH: add stdin as a marked block at the end of PATH (once)
add_block() {
    local body nl
    body=$(cat)
    # already there and the same: nothing to write
    if [ -f "$R$1" ] && grep -q -x -F -e "$BLOCK_BEGIN" "$R$1" && [ "$(block_body "$R$1")" = "$body" ]; then
        manifest_prefix_has "block${TAB}$1${TAB}" || manifest_add "block${TAB}$1${TAB}"
        return 0
    fi
    rw_for "$1" || return 1
    [ -f "$R$1" ] || bk_file "$1"
    strip_block "$R$1"
    nl=
    [ -s "$R$1" ] && [ -n "$(tail -c 1 "$R$1")" ] && nl=nl
    manifest_prefix_has "block${TAB}$1${TAB}" || manifest_add "block${TAB}$1${TAB}$nl"
    mkdir -p "$(dirname "$R$1")"
    {
        [ -n "$nl" ] && echo
        printf '%s\n%s\n%s\n' "$BLOCK_BEGIN" "$body" "$BLOCK_END"
    } >> "$R$1"
}

# strip_block FILE [nl]: remove our block; nl: also the newline we added before it
strip_block() {
    [ -f "$1" ] || return 0
    grep -q -x -F -e "$BLOCK_BEGIN" "$1" || return 0   # nothing of ours: leave the file byte for byte
    awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '
        $0 == b { skip = 1; next }
        skip && $0 == e { skip = 0; next }
        !skip { print }
    ' "$1" > "$1.usbnp.tmp" || return 1
    if [ "$2" = nl ]; then
        # awk wrote the last line with a newline it didn't have: drop that byte
        head -c "$(($(wc -c < "$1.usbnp.tmp") - 1))" "$1.usbnp.tmp" > "$1"
    else
        cat "$1.usbnp.tmp" > "$1"
    fi
    rm -f "$1.usbnp.tmp"
}

# undo_all: reverse every recorded change, newest first
undo_all() {
    [ -f "$STATE/manifest" ] || return 0
    awk '{ a[NR] = $0 } END { for (i = NR; i > 0; i--) print a[i] }' "$STATE/manifest" |
    while IFS="$TAB" read -r kind path a _style c d; do
        case "$kind" in
            file)
                if [ "$a" = saved ]; then
                    cp -p "$STATE/backup/$(save_name "$path")" "$R$path" 2>/dev/null || cp "$STATE/backup/$(save_name "$path")" "$R$path"
                else
                    rm -f "$R$path"
                fi
                ;;
            key)
                # a=KEY b=STYLE c=saved|absent d=old line
                if [ -f "$R$path" ]; then
                    if [ "$c" = saved ]; then
                        replace_key_line "$R$path" "$(key_regex "$a")" "$d"
                    else
                        replace_key_line "$R$path" "$(key_regex "$a")" ""
                    fi
                fi
                ;;
            block)
                rw_for "$path" && strip_block "$R$path" "$a"
                rw_done
                ;;
        esac
    done
    rm -f "$STATE/manifest"
    rm -rf "$STATE/backup"
}

# --- the runtime hosts file ----------------------------------------------------
# The name usb-netplay is RetroArch's pre-filled netplay host. It points at the
# handheld on the other end of the newest cable link.
NETPLAY_NAME=usb-netplay

hosts_set() {
    local f tmp
    f=$(resolve /etc/hosts)
    [ -f "$f" ] || return 1
    tmp="$f.usbnp.$$"
    {
        grep -v "[[:space:]]$1\$" "$f"
        if [ -n "$2" ]; then printf '%s\t%s\n' "$2" "$1"; fi
    } > "$tmp"
    chmod 644 "$tmp" && mv -f "$tmp" "$f"
}

hosts_get() {
    awk -v n="$1" '$2 == n && NF == 2 { print $1; exit }' "$(resolve /etc/hosts)" 2>/dev/null
}
