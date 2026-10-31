# Showing text on the handheld's screen from a menu script. Sourced, not run.
# A Ports/Tools script's stdout goes to a log on most firmwares, so each one
# needs its own way; the full report is always written to a file as well.
# shellcheck shell=sh

# ui_notify TEXT: a short notice in the menu, through EmulationStation's local
# web API (ROCKNIX, AmberELEC, KNULLI, Batocera). Nothing happens where the
# frontend has no such API or isn't running. NOTIFY=no in settings.conf turns
# it off.
ui_notify() {
    [ "$NOTIFY" = no ] && return 0
    [ -n "$USBNP_DRYRUN" ] && { run notify "$1"; return 0; }
    case "$FW" in rocknix|amberelec|knulli|batocera) : ;; *) return 0 ;; esac
    if have curl; then
        curl -s -m 2 -o /dev/null -X POST --data-binary "$1" http://127.0.0.1:1234/notify 2>/dev/null
    elif [ -n "$PY" ]; then
        "$PY" -c 'import sys, urllib.request as u; u.urlopen(u.Request("http://127.0.0.1:1234/notify", sys.argv[1].encode()), timeout=2)' "$1" 2>/dev/null
    fi
    return 0
}

# ui_show TITLE FILE: show the report in FILE (first lines on small notifiers)
ui_show() {
    local title file short
    title=$1 file=$2
    short=$(head -n 6 "$file" | tr '\n' ' ' | cut -c1-300)
    [ -n "$USBNP_DRYRUN" ] && { run ui "$title"; return 0; }
    case "$FW" in
        knulli|batocera)
            # EmulationStation's local web API; shown when the menu comes back
            if have curl && curl -s -m 2 http://127.0.0.1:1234/caps >/dev/null 2>&1; then
                curl -s -m 3 -X POST http://127.0.0.1:1234/messagebox --data-binary "@$file" >/dev/null 2>&1 && return 0
            fi ;;
        rocknix|amberelec)
            if have text_viewer; then
                # -w wraps long lines (640x480 screens); which button closes
                # it differs per controller, so it also closes by itself
                printf '\n(This closes by itself in 25 seconds.)\n' >> "$file"
                if have timeout; then
                    timeout 25 text_viewer -w -t "$title" -f 20 "$file" >/dev/null 2>&1
                else
                    text_viewer -w -t "$title" -f 20 "$file" >/dev/null 2>&1
                fi
                return 0
            fi
            if have sdl2notify; then
                sdl2notify --center "$title||$short" 255 255 255 10 >/dev/null 2>&1 && return 0
            fi ;;
        muos)
            if [ -x /opt/muos/frontend/muxmessage ]; then
                /opt/muos/frontend/muxmessage 0 "$title: $short" >/dev/null 2>&1
                sleep 6
                return 0
            fi ;;
    esac
    # plain console (ArkOS/dArkOS Tools run on tty1; AmberELEC's console)
    for t in /dev/tty1 /dev/tty0 /dev/console; do
        if [ -w "$t" ]; then
            {
                printf '\033c'
                printf '%s\n\n' "$title"
                cat "$file"
                printf '\n(closing in 15 seconds)\n'
            } > "$t" 2>/dev/null && { sleep 15; return 0; }
        fi
    done
    return 1
}
