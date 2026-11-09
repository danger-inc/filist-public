#!/usr/bin/env bash
# Baut libfilist-pick.so und legt einen Gnumeric-Starter an, der filists Dateiauswahl benutzt.
# Rückgängig: ./install.sh --undo
set -e
here=$(cd "$(dirname "$0")" && pwd)
lib="$HOME/.local/lib/libfilist-pick.so"
desk="$HOME/.local/share/applications/org.gnumeric.gnumeric.desktop"
if [ "$1" = "--undo" ]; then rm -f "$lib" "$desk"; update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true; echo "entfernt"; exit 0; fi
mkdir -p "$HOME/.local/lib" "$HOME/.local/share/applications"
gcc -O2 -Wall -Wno-deprecated-declarations -shared -fPIC -o "$lib" "$here/filist-pick.c" $(pkg-config --cflags --libs gtk+-3.0) -ldl
src=/usr/share/applications/org.gnumeric.gnumeric.desktop
[ -f "$src" ] || { echo "Gnumeric-Starter fehlt ($src)"; exit 1; }
# Dunkles System-Theme gewählt, aber nicht installiert (z. B. adw-gtk3-dark)? Dann fällt GTK3 aufs helle Standard-Theme
# zurück und dunkle Icon-Themes (Papirus-Dark) werden blass → für Gnumeric Adwaita:dark erzwingen.
envs="LD_PRELOAD=$lib"
theme=$(gsettings get org.gnome.desktop.interface gtk-theme 2>/dev/null | tr -d "'" || true)
scheme=$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null | tr -d "'" || true)
if [ "$scheme" = prefer-dark ] && [ -n "$theme" ] && [ ! -d "/usr/share/themes/$theme" ] && [ ! -d "$HOME/.themes/$theme" ] \
   && [ ! -d "$HOME/.local/share/themes/$theme" ]; then
    envs="GTK_THEME=Adwaita:dark $envs"
fi
sed "s|^Exec=gnumeric |Exec=env $envs gnumeric |" "$src" > "$desk"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "fertig – Gnumeric über den Starter (neu) öffnen; ein schon laufendes Gnumeric einmal beenden"
