#!/usr/bin/env bash
# Starter für filist. Läuft der Hintergrunddienst schon, geht der Auftrag direkt per D-Bus hin
# (kein Python-Start → Fenster sofort da). Sonst wird filist gestartet.
#   filist [ordner|datei …]   filist --daemon   filist --check
APP=de.invincible.Filist
OBJ=/de/invincible/Filist
MAIN="${FILIST_HOME:-$HOME/.local/share/filist/app}/filist.py"

# ohne gdbus (Paket libglib2.0-bin/glib2) direkt starten – etwas langsamer, geht aber
command -v gdbus >/dev/null 2>&1 || exec python3 "$MAIN" "$@"

running() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$APP" 2>/dev/null | grep -q true; }

start() {
    # bevorzugt als systemd-Benutzerdienst (Neustart nach Absturz, sauberer Start über D-Bus)
    if systemctl --user cat filist.service >/dev/null 2>&1; then
        systemctl --user start filist.service && return
    fi
    setsid -f python3 "$MAIN" "$@" >"${XDG_RUNTIME_DIR:-/tmp}/filist.log" 2>&1 </dev/null
}

case "${1:-}" in
    --daemon) running || start --daemon; exit 0 ;;
    --check)  exec python3 "$MAIN" --check ;;
esac

if ! running; then
    start --daemon
    for _ in $(seq 50); do running && break; sleep 0.05; done
fi

if [ $# -eq 0 ]; then
    exec gdbus call --session --dest "$APP" --object-path "$OBJ" \
        --method org.freedesktop.Application.Activate "{}" >/dev/null
fi

urlencode() {      # Pfad → URI-Teil (UTF-8 byteweise, wie Gio es erwartet) – ohne Python-Start
    local LC_ALL=C s="$1" out="" c i
    for (( i = 0; i < ${#s}; i++ )); do
        c="${s:i:1}"
        case "$c" in [a-zA-Z0-9/._~-]) out+="$c" ;; *) printf -v c '%%%02X' "'$c"; out+="$c" ;; esac
    done
    printf '%s' "$out"
}

dirs=(); files=()
for a in "$@"; do
    if [[ "$a" == *://* || "$a" == trash:* || "$a" == recent:* ]]; then dirs+=("'$a'"); continue; fi
    p="$(realpath -m -- "$a")"
    uri="file://$(urlencode "$p")"
    if [ -d "$p" ]; then dirs+=("'$uri'"); else files+=("'$uri'"); fi
done
join() { local IFS=,; echo "[$*]"; }
[ ${#dirs[@]} -gt 0 ] && gdbus call --session --dest "$APP" --object-path "$OBJ" \
    --method org.freedesktop.Application.Open "$(join "${dirs[@]}")" "{}" >/dev/null
[ ${#files[@]} -gt 0 ] && gdbus call --session --dest org.freedesktop.FileManager1 \
    --object-path /org/freedesktop/FileManager1 --method org.freedesktop.FileManager1.ShowItems \
    "$(join "${files[@]}")" "" >/dev/null
exit 0
