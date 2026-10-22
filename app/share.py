# „teilen“ im Rechtsklick-Menü: Dateien an andere Geräte oder Leute schicken.
# Jeder Weg erscheint nur, wenn das Programm dafür da ist:
#   quick share  → sharist (Android-Handys in der Nähe, Suite-App)
#   per mail     → mailist (neue Mail mit Anhängen) oder sonst das Standard-Mailprogramm (xdg-email)
#   bluetooth    → blueman-sendto bzw. bluetooth-sendto
#   als qr-code  → Pfad/Text einer kleinen Textdatei als QR zum Abfotografieren (qrencode)
import os, shutil, subprocess, urllib.parse

from gi.repository import GLib


def _bin(name):
    p = shutil.which(name) or os.path.expanduser(f"~/.local/bin/{name}")
    return p if os.path.exists(p) else None


def ways(paths):
    """Mögliche Wege für diese Dateien → [(id, text)]"""
    files = [p for p in paths if p and os.path.isfile(p)]
    out = []
    if not files:
        return out
    if _bin("sharist"):
        out.append(("quick", "quick share (handy) …"))
    if _bin("mailist") or shutil.which("xdg-email"):
        out.append(("mail", "per mail …"))
    if shutil.which("blueman-sendto") or shutil.which("bluetooth-sendto"):
        out.append(("bt", "bluetooth …"))
    if len(files) == 1 and shutil.which("qrencode") and _small_text(files[0]):
        out.append(("qr", "inhalt als qr-code"))
    return out


def _small_text(path):
    try:
        if os.path.getsize(path) > 1500:
            return False
        with open(path, "rb") as f:
            f.read().decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def _spawn(argv):
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True)


def share(way, paths, win):
    files = [p for p in paths if p and os.path.isfile(p)]
    if not files:
        win.say("teilen geht nur mit dateien", error=True)
        return
    n = f"„{os.path.basename(files[0])}“" if len(files) == 1 else f"{len(files)} dateien"
    try:
        if way == "quick":
            _spawn([_bin("sharist"), *files])
            win.say(f"quick share: {n} – handy in der nähe auswählen")
        elif way == "mail":
            if _bin("mailist"):
                q = "&".join("attach=" + urllib.parse.quote(f) for f in files)
                subj = urllib.parse.quote(os.path.basename(files[0]) if len(files) == 1 else f"{len(files)} dateien")
                _spawn([_bin("mailist"), f"mailto:?subject={subj}&{q}"])
            else:
                argv = ["xdg-email"]
                for f in files:
                    argv += ["--attach", f]
                _spawn(argv)
            win.say(f"neue mail mit {n}")
        elif way == "bt":
            tool = shutil.which("blueman-sendto") or shutil.which("bluetooth-sendto")
            _spawn([tool, *files])
            win.say(f"bluetooth: {n} – gerät auswählen")
        elif way == "qr":
            show_qr(win, open(files[0], encoding="utf-8").read().strip(), os.path.basename(files[0]))
    except (OSError, TypeError) as e:
        win.say(f"teilen fehlgeschlagen: {e}", error=True)


def show_qr(win, text, title):
    """QR-Code in einem kleinen Dialog (qrencode → PNG → Bild)."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gtk
    import dialogs
    try:
        png = subprocess.run(["qrencode", "-o", "-", "-s", "8", "-m", "2", text], capture_output=True,
                             timeout=10).stdout
        tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
    except (OSError, subprocess.TimeoutExpired, GLib.Error) as e:
        win.say(f"qr-code: {e}", error=True)
        return
    d = dialogs.Dialog(win, f"qr · {title}", width=380)
    pic = Gtk.Picture(paintable=tex, can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=320)
    d.body.append(pic)
    d.button("schließen", d.close, default=True)
    d.present()
