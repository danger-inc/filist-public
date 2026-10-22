#!/usr/bin/env bash
# filist als Datei-Dialog („Öffnen“/„Speichern unter“) für alle Programme einrichten – ohne root.
#   ./setup-portal.sh          einrichten
#   ./setup-portal.sh --undo   rückgängig machen (wieder der vorherige Dialog)
# Funktionsweise: xdg-desktop-portal bekommt einen eigenen Portal-Ordner (Verknüpfungen auf die System-
# Portale + filist.portal) und eine Vorgabe „FileChooser=filist“. GTK-Programme nutzen das Portal nur mit
# GDK_DEBUG=portals (GTK4) bzw. GTK_USE_PORTAL=1 (GTK3) – das kommt in die Sitzungsumgebung.
set -euo pipefail
DIR="$HOME/.local/share/xdg-desktop-portal/portals"
DROPIN="$HOME/.config/systemd/user/xdg-desktop-portal.service.d/filist.conf"
CONFDIR="$HOME/.config/xdg-desktop-portal"
ENVD="$HOME/.config/environment.d/90-filist.conf"
DESK="$(echo "${XDG_CURRENT_DESKTOP:-}" | cut -d: -f1 | tr '[:upper:]' '[:lower:]')"
MARK="# filist"

restart_portal() {
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart xdg-desktop-portal.service 2>/dev/null || true
}

if [[ "${1:-}" == "--undo" ]]; then
    rm -f "$DROPIN" "$ENVD"
    for f in "$CONFDIR/portals.conf" ${DESK:+"$CONFDIR/$DESK-portals.conf"}; do
        [ -f "$f" ] && grep -qE "^# (filist|filewise)" "$f" && rm -f "$f"
    done
    rm -rf "$DIR"
    systemctl --user unset-environment GDK_DEBUG GTK_USE_PORTAL 2>/dev/null || true
    restart_portal
    echo "Datei-Dialog zurückgesetzt. Programme danach einmal neu starten."
    exit 0
fi

mkdir -p "$DIR" "$(dirname "$DROPIN")" "$CONFDIR" "$(dirname "$ENVD")"
# System-Portale weiter anbieten (Verknüpfungen, damit Updates ankommen)
for p in /usr/share/xdg-desktop-portal/portals/*.portal; do
    [ -e "$p" ] && ln -sf "$p" "$DIR/$(basename "$p")"
done
cat > "$DIR/filist.portal" <<P
[portal]
DBusName=org.freedesktop.impl.portal.desktop.filist
Interfaces=org.freedesktop.impl.portal.FileChooser;
P
cat > "$DROPIN" <<D
[Service]
Environment=XDG_DESKTOP_PORTAL_DIR=%h/.local/share/xdg-desktop-portal/portals
D
# bisherige Vorgabe übernehmen, nur den Datei-Dialog umstellen
default="gtk"
for src in "/usr/share/xdg-desktop-portal/$DESK-portals.conf" /usr/share/xdg-desktop-portal/portals.conf; do
    if [ -f "$src" ]; then
        d="$(grep -E '^default=' "$src" | head -1 | cut -d= -f2 || true)"
        [ -n "$d" ] && default="$d" && break
    fi
done
# Mit XDG_DESKTOP_PORTAL_DIR liest das Portal die Konfiguration NUR aus diesem Ordner → dort und in ~/.config
for f in "$CONFDIR/portals.conf" ${DESK:+"$CONFDIR/$DESK-portals.conf"} "$DIR/portals.conf" ${DESK:+"$DIR/$DESK-portals.conf"}; do
    if [ -f "$f" ] && ! [ -L "$f" ] && ! grep -qE "^# (filist|filewise)" "$f"; then
        cp "$f" "$f.vor-filist"
    fi
    printf '%s – Datei-Dialog über filist (setup-portal.sh --undo stellt zurück)\n[preferred]\ndefault=%s\norg.freedesktop.impl.portal.FileChooser=filist\n' "$MARK" "$default" > "$f"
done
printf '%s\nGDK_DEBUG=portals\nGTK_USE_PORTAL=1\n' "$MARK" > "$ENVD"
systemctl --user set-environment GDK_DEBUG=portals GTK_USE_PORTAL=1 2>/dev/null || true
command -v dbus-update-activation-environment >/dev/null && \
    GDK_DEBUG=portals GTK_USE_PORTAL=1 dbus-update-activation-environment --systemd GDK_DEBUG GTK_USE_PORTAL 2>/dev/null || true
restart_portal
echo "filist ist jetzt der Datei-Dialog."
echo "Hinweis: Programme, die schon laufen, einmal neu starten. Unter Hyprland zusätzlich in die Konfiguration:"
echo '  env = GDK_DEBUG,portals   und   env = GTK_USE_PORTAL,1   (bzw. hl.env(...) in hyprland.lua)'
