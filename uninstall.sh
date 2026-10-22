#!/usr/bin/env bash
# filist entfernen. Einstellungen (~/.config/filist) bleiben, Lesezeichen gehören GTK und bleiben auch.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -x "$SRC/setup-portal.sh" ] && "$SRC/setup-portal.sh" --undo || true
systemctl --user stop filist.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/filist.service"
systemctl --user daemon-reload 2>/dev/null || true
rm -rf "$HOME/.local/share/filist/app"
rm -f "$HOME/.local/bin/filist" "$HOME/.local/share/applications/filist.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/filist.svg"
for name in de.invincible.Filist org.freedesktop.impl.portal.desktop.filist org.freedesktop.FileManager1; do
    rm -f "$HOME/.local/share/dbus-1/services/$name.service"
done
[ "$(xdg-mime query default inode/directory 2>/dev/null)" = "filist.desktop" ] && echo "Hinweis: Standard-Dateimanager neu wählen, z. B. xdg-mime default thunar.desktop inode/directory"
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1 || true
echo "filist entfernt."
