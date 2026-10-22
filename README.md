# filist

Datei-Explorer für Linux (GTK4) im Stil der Suite (datist, musicalist, notist, scourge):
schnell, mit Tabs, geteilter Ansicht, Vorschau – und auf Wunsch der Datei-Dialog („Öffnen“/„Speichern
unter“) für **alle** Programme.

## Installieren

```bash
git clone https://github.com/invincible-nix/filist-public ~/filist-src
cd ~/filist-src
./install.sh
```

Der Installer fragt nach den Systempaketen (einmal Passwort) und dann zwei Dinge, beide standardmäßig **nein**:

- **Standard-Dateimanager?** – Ordner öffnen und „Im Ordner anzeigen“ (z. B. aus dem Browser) gehen dann zu filist.
- **Datei-Dialog für alle Programme?** – „Öffnen“/„Speichern unter“ kommen dann von filist. Tiefer Eingriff,
  nur sinnvoll auf Hyprland/Sway; rückgängig mit `./setup-portal.sh --undo`.

Ohne beides ist filist einfach ein zusätzlicher Datei-Explorer – dein bisheriger Dateimanager bleibt unberührt.
Starten: `filist` oder über das Anwendungsmenü. Entfernen: `./uninstall.sh` (nimmt auch den Datei-Dialog zurück).

**Hyprland:** `bind = SUPER, E, exec, filist` und, falls du den Datei-Dialog willst, unter `env`:
`GDK_DEBUG,portals` sowie `GTK_USE_PORTAL,1`. Firefox/Zen zusätzlich:
`about:config` → `widget.use-xdg-desktop-portal.file-picker` = `1`.

## Aktualisieren

```bash
cd ~/filist-src && ./update.sh
```

## Läuft auf

| System | Status |
|---|---|
| Ubuntu 24.04+, Linux Mint 22+, Debian 13 | getestet (Ubuntu 24.04) |
| Fedora 39+ | getestet (Fedora 44) |
| Arch, EndeavourOS, CachyOS, Manjaro | getestet (Arch) |
| openSUSE Tumbleweed | sollte gehen (ungetestet) |
| Windows / macOS | nein – filist baut auf Linux-Diensten auf (gvfs, Papierkorb, D-Bus, Portal) |

Nötig ist **GTK 4.12 oder neuer** (Ubuntu 22.04 / Mint 21 / Debian 12 sind zu alt). Mit systemd läuft filist
als Benutzerdienst im Hintergrund (Fenster sofort da); ohne systemd startet es bei Bedarf über D-Bus.

## Was alles drin ist

- **Tabs** (Strg+T, Strg+W, Alt+1–9) und **geteilte Ansicht** (F3, Tab wechselt die Hälfte, F5/F6 kopiert/verschiebt hinüber)
- **Liste** (sortierbar per Spaltenkopf) und **Raster** mit Vorschaubildern (Bilder, Videos, PDF …), Zoom mit Strg ±
- **Seitenleiste**: Ordner, Lesezeichen (gleiche wie GTK/Thunar), Laufwerke mit Füllstand, einhängen/auswerfen,
  Netzwerk (sftp/smb/ftp), Papierkorb
- **Pfadleiste**: klicken oder Strg+L tippen (Tab vervollständigt), `/` und `~` springen direkt hinein
- **Filtern** beim Tippen, **Enter sucht in allen Unterordnern**; in der Liste einfach lostippen springt zum Namen
- **Kopieren/Ausschneiden/Einfügen** – auch mit anderen Programmen (Thunar, Nautilus, Browser); Bilder und Text aus der
  Zwischenablage werden als Datei eingefügt
- **Ziehen & Ablegen** (Strg = kopieren, Umschalt = verschieben, Strg+Umschalt = Verknüpfung), auch auf Ordner, Tabs,
  Pfad und Seitenleiste
- Fortschritt mit Tempo und Restzeit, **Abbrechen**, Nachfrage bei Konflikten (ersetzen, überspringen, beide
  behalten, zusammenführen – auch „für alle“)
- **Rückgängig** (Strg+Z) für Umbenennen, Verschieben, Kopieren, Papierkorb, Neu
- **Umbenennen direkt in der Zeile** (F2, wie im Windows-Explorer), **mehrere umbenennen** mit Suchen/Ersetzen,
  Regex, Nummern und Live-Vorschau; neue Ordner/Dateien werden gleich in der Zeile benannt
- **Teilen** (Rechtsklick): Quick Share ans Handy (mit sharist), per Mail mit Anhang, Bluetooth, QR-Code
- **Netzlaufwerke hinter VPN**: Rechtsklick auf den Ort → „vpn vorher verbinden“ – beim Öffnen verbindet filist
  das VPN (NetworkManager, eduVPN) selbst und zeigt solange einen Ladekreis
- **Papierkorb** mit Wiederherstellen, endgültig löschen (Umschalt+Entf), leeren
- **Packen** (zip, tar.gz, tar.xz, 7z) und **Entpacken** (fast alle Formate)
- **Vorschau** (Leertaste): Bilder, Text, PDF/Video-Standbild, Ordnerinhalt, Infos
- **Eigenschaften** (Alt+Enter): Größe (auch ganzer Ordner), Rechte ändern (auch rekursiv), Besitzer, Standard-Programm,
  Prüfsummen (md5/sha1/sha256)
- **Öffnen mit** (alle Programme, durchsuchbar, als Standard merken, eigener Befehl), Terminal hier (F4)
- Versteckte Dateien (Strg+H), Auswahl nach Muster (Strg+S), umkehren (Strg+I)
- Schmale Fenster: Seitenleiste und Spalten blenden sich automatisch aus

## Tastenkürzel

| Taste | Aktion | Taste | Aktion |
|---|---|---|---|
| Enter | öffnen | Alt+← / → / ↑ | zurück / vor / hoch |
| Strg+T / Strg+W | Tab auf / zu | Rücktaste | Ordner hoch |
| Strg+L | Pfad eingeben | Strg+F | filtern / suchen |
| F2 | umbenennen | Entf / Umschalt+Entf | Papierkorb / löschen |
| Strg+C / X / V | kopieren / ausschneiden / einfügen | Strg+Z | rückgängig |
| Strg+Umschalt+N | neuer Ordner | Strg+Alt+N | neue Datei |
| Strg+H | versteckte Dateien | Leertaste | Vorschau |
| F3 | teilen | F5 / F6 | in andere Hälfte kopieren / verschieben |
| F4 | Terminal hier | F9 | Seitenleiste |
| Strg+1 / Strg+2 | Liste / Raster | Strg+D | Lesezeichen |
| Alt+Enter | Eigenschaften | Strg+Umschalt+C | Pfad kopieren |

## Was es braucht

GTK 4, PyGObject. Empfohlen: **Tumbler** (Vorschaubilder; Videos zusätzlich über ffmpeg), **bsdtar/7z** (Entpacken),
**gvfs** (Papierkorb, Netzwerk, „zuletzt benutzt“), **xdg-desktop-portal** (Datei-Dialog). `filist --check` zeigt,
was fehlt. Die Symbole bringt filist selbst mit.

## Probleme?

- **Kästchen statt Symbolen:** `./update.sh` – die Symbolschrift kommt mit und wird automatisch geladen.
- **`filist: Befehl nicht gefunden`:** `~/.local/bin` fehlt im PATH (der Installer zeigt, was in die `~/.bashrc`
  gehört). Aus dem Anwendungsmenü startet es auch so.
- **„Im Ordner anzeigen“ öffnet plötzlich filist:** `./install.sh` erneut und bei „Standard-Dateimanager?“ nein
  sagen, oder `xdg-mime default org.gnome.Nautilus.desktop inode/directory` (bzw. dolphin/thunar).
- **Datei-Dialog spinnt:** `./setup-portal.sh --undo`, abmelden, anmelden.
