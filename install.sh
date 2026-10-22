#!/usr/bin/env bash
# filist installieren (ins eigene Benutzerverzeichnis, root nur für Systempakete).
#   ./install.sh            normal (fragt nach Paketen, Standard-Dateimanager, Datei-Dialog)
#   ./install.sh --update   ohne Rückfragen (von update.sh)
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SRC/pkg.sh"
APP_DIR="$HOME/.local/share/filist/app"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
DBUS="$HOME/.local/share/dbus-1/services"
UPDATE=0; [[ "${1:-}" == "--update" ]] && UPDATE=1
have_systemd() { command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; }

say "== filist installieren =="
if ask "Systempakete installieren (GTK4, PyGObject, Vorschaubilder, Entpacken, Papierkorb/Netzwerk)?"; then
    pkg_install \
        "gtk4 python python-gobject python-cairo glib2 tumbler ffmpegthumbnailer poppler-glib libarchive 7zip gvfs gvfs-smb xdg-utils xdg-desktop-portal adwaita-icon-theme" \
        "python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 libglib2.0-bin tumbler tumbler-plugins-extra ffmpegthumbnailer libarchive-tools 7zip gvfs gvfs-backends xdg-utils xdg-desktop-portal adwaita-icon-theme" \
        "python3 python3-gobject python3-cairo gtk4 glib2 tumbler ffmpegthumbnailer bsdtar p7zip p7zip-plugins gvfs gvfs-smb xdg-utils xdg-desktop-portal adwaita-icon-theme" \
        "python3 python3-gobject python3-gobject-cairo python3-gobject-Gdk typelib-1_0-Gtk-4_0 glib2-tools tumbler ffmpegthumbnailer bsdtar 7zip gvfs gvfs-backends xdg-utils xdg-desktop-portal adwaita-icon-theme" \
        || warn "Paketinstallation nicht vollständig – weiter."
fi
echo

# alter Name „filewise“ (bis 2026-10): Dienst, Starter, Menüeintrag und D-Bus-Dateien entfernen
OLD_PORTAL=0
[ -e "$HOME/.local/share/xdg-desktop-portal/portals/filewise.portal" ] && OLD_PORTAL=1
if [ -e "$HOME/.config/systemd/user/filewise.service" ]; then
    systemctl --user stop filewise.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/filewise.service"
fi
rm -f "$BIN/filewise" "$APPS/filewise.desktop" "$ICONS/filewise.svg" \
      "$DBUS/de.invincible.Filewise.service" "$DBUS/org.freedesktop.impl.portal.desktop.filewise.service" \
      "$HOME/.local/share/xdg-desktop-portal/portals/filewise.portal" \
      "$HOME/.config/systemd/user/xdg-desktop-portal.service.d/filewise.conf" \
      "$HOME/.config/environment.d/90-filewise.conf"
if [ -d "$HOME/.local/share/filewise/app" ] && [ ! -d "$HOME/.local/share/filewise/packaging" ]; then
    rm -rf "$HOME/.local/share/filewise"
fi
if grep -qs "inode/directory=filewise.desktop" "$HOME/.config/mimeapps.list"; then
    xdg-mime default filist.desktop inode/directory 2>/dev/null || true
fi

say "Kopiere filist nach $APP_DIR …"
mkdir -p "$APP_DIR" "$BIN" "$APPS" "$ICONS" "$DBUS"
APP_SRC="$SRC/app"; [ -d "$APP_SRC" ] || APP_SRC="$SRC/../app"     # Repo: app/ daneben, lokal: ../app
if [ "$(realpath "$APP_SRC")" != "$(realpath "$APP_DIR")" ]; then
    rm -rf "$APP_DIR.neu"
    cp -r "$APP_SRC" "$APP_DIR.neu"
    rm -rf "$APP_DIR"
    mv "$APP_DIR.neu" "$APP_DIR"
fi
install -m 755 "$SRC/filist.sh" "$BIN/filist"
sed "s|@EXEC@|$BIN/filist|g" "$SRC/filist.desktop.in" > "$APPS/filist.desktop"
cp -f "$APP_DIR/logo/filist.svg" "$ICONS/filist.svg"

# Hintergrunddienst: mit systemd als Benutzerdienst, D-Bus startet ihn bei Bedarf darüber (SystemdService=).
# Wichtig: nie über ein Skript, das sich abkoppelt – sonst hält der Bus die Aktivierung für gescheitert.
write_dbus() {   # write_dbus <name>
    if have_systemd; then
        printf '[D-BUS Service]\nName=%s\nExec=/usr/bin/env python3 %s/filist.py --daemon\nSystemdService=filist.service\n' \
            "$1" "$APP_DIR" > "$DBUS/$1.service"
    else
        printf '[D-BUS Service]\nName=%s\nExec=/usr/bin/env python3 %s/filist.py --daemon\n' "$1" "$APP_DIR" > "$DBUS/$1.service"
    fi
}
if have_systemd; then
    mkdir -p "$HOME/.config/systemd/user"
    cp -f "$SRC/filist.service" "$HOME/.config/systemd/user/filist.service"
    systemctl --user daemon-reload 2>/dev/null || true
fi
write_dbus de.invincible.Filist
write_dbus org.freedesktop.impl.portal.desktop.filist

# Standard-Dateimanager: nur dann übernimmt filist auch „Im Ordner anzeigen“ (org.freedesktop.FileManager1)
IS_DEFAULT=0
if command -v xdg-mime >/dev/null 2>&1 && [ "$(xdg-mime query default inode/directory 2>/dev/null)" = "filist.desktop" ]; then
    IS_DEFAULT=1
fi
if (( ! IS_DEFAULT )) && ask "filist als Standard-Dateimanager (Ordner öffnen, „Im Ordner anzeigen“)?" n; then
    if xdg-mime default filist.desktop inode/directory 2>/dev/null; then IS_DEFAULT=1
    else warn "xdg-mime fehlt (Paket xdg-utils) – Standard-Dateimanager nicht gesetzt."; fi
fi
if (( IS_DEFAULT )); then
    write_dbus org.freedesktop.FileManager1
else
    rm -f "$DBUS/org.freedesktop.FileManager1.service"
fi
# D-Bus soll die neuen Dienstdateien sofort kennen (sonst erst nach dem nächsten Anmelden)
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
refresh_desktop

# Datei-Dialog für alle Programme: tiefer Eingriff ins System → standardmäßig NEIN
if (( OLD_PORTAL )) || ask "filist auch als Datei-Dialog („Öffnen“/„Speichern unter“) für alle Programme? (rückgängig: ./setup-portal.sh --undo)" n; then
    "$SRC/setup-portal.sh" || warn "Datei-Dialog konnte nicht eingerichtet werden – filist selbst läuft trotzdem."
fi

# laufenden Dienst neu starten, damit die neue Version greift
if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
    if have_systemd; then
        systemctl --user restart filist.service 2>/dev/null || "$BIN/filist" --daemon || true
    else
        pkill -f "^python3 $APP_DIR/filist.py" 2>/dev/null || true
        "$BIN/filist" --daemon || true
    fi
fi
echo
say "Prüfe …"
check_gtk || true
python3 "$APP_DIR/filist.py" --check 2>/dev/null | tail -n +3 || true
echo
path_hint
say "Fertig!  Starten mit:  filist   (oder über das Anwendungsmenü)"
echo "Aktualisieren: ./update.sh   Entfernen: ./uninstall.sh"
