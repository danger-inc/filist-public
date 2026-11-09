# Gnumeric mit filist als Dateidialog

Gnumeric (GTK3) baut seinen „Öffnen“/„Speichern unter“-Dialog als eigenes Widget und kennt das Datei-Portal nicht –
`GTK_USE_PORTAL=1` hilft hier nicht. `filist-pick.c` ist ein kleiner `LD_PRELOAD`-Shim, der die eine Funktion
`go_gtk_file_sel_dialog` (libgoffice) ersetzt: Zustand des Dialogs (Aktion, Filter, Name, Ordner, Dateityp-Auswahl,
„nur lesen“) geht an `org.freedesktop.portal.FileChooser` (filist), das Ergebnis wird in den Dialog zurückgeschrieben.
Ist das Portal nicht erreichbar, erscheint der normale GTK-Dialog.

    ./install.sh          # baut ~/.local/lib/libfilist-pick.so + Starter ~/.local/share/applications/org.gnumeric.gnumeric.desktop
    ./install.sh --undo

Voraussetzungen: gcc, GTK3-Entwicklungsdateien (`pkg-config gtk+-3.0`), filist als Portal (`../setup-portal.sh`).
Ein schon laufendes Gnumeric einmal beenden und über den Starter neu öffnen. Debug: `FILIST_SHIM_DEBUG=1`, aus: `FILIST_SHIM_OFF=1`.

Hyprland: filist-Dialoge sollen schwebend und zentriert erscheinen und ein maximiertes Gnumeric nicht verkleinern.
Hyprland kennt den Fenstertitel beim Öffnen noch nicht, eine Titel-Regel greift deshalb nicht – stattdessen ein
`window.open`-Handler in `hyprland.lua` (siehe `hyprland-chooser.lua` hier).
