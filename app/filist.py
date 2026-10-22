#!/usr/bin/env python3
# filist – Datei-Explorer im Stil der Suite (datist, musicalist, notist, scourge).
# Läuft als Hintergrunddienst (filist --daemon), damit Fenster sofort aufgehen; stellt außerdem den
# Datei-Dialog für andere Programme (Portal) und „Im Ordner anzeigen“ (FileManager1) bereit.
#   filist [ordner|datei|uri …]   Fenster öffnen (Datei → Ordner öffnen und Datei markieren)
#   filist --daemon               nur im Hintergrund starten
#   filist --check                Abhängigkeiten prüfen
import json, os, sys

# filist ist selbst der Datei-Dialog des Portals: es darf beim Start nicht seinerseits das Portal fragen
# (GTK tut das mit GDK_DEBUG=portals) – sonst warten Portal und filist beim automatischen Start aufeinander.
# GTK4 fragt das Portal sonst auch von sich aus (Farbschema, Schrift) → für filist ganz abschalten.
os.environ.pop("GTK_USE_PORTAL", None)
os.environ["GDK_DEBUG"] = "no-portals"

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.expanduser("~/.local/share/rice-apps"))   # live tui.py/glass.py der Suite …
sys.path.append(os.path.join(HERE, "vendor"))                        # … sonst die mitgelieferten Kopien

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk


def load_icon_font():
    """Mitgelieferte Symbolschrift laden, damit Icons auch ohne passende Nerd Font erscheinen."""
    path = os.path.join(HERE, "fonts", "notes-icons.ttf")
    if not os.path.exists(path):
        return
    try:
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        fm = PangoCairo.FontMap.get_default()
        if hasattr(fm, "add_font_file"):
            fm.add_font_file(path)
            return
    except Exception:
        pass
    try:
        import ctypes, ctypes.util
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        fc.FcConfigGetCurrent.restype = ctypes.c_void_p
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fc.FcConfigAppFontAddFile(fc.FcConfigGetCurrent(), path.encode())
    except Exception:
        pass


load_icon_font()

import tui  # noqa: E402
import model  # noqa: E402
import ops as opsmod  # noqa: E402

APP_ID = os.environ.get("FILIST_APP_ID", "de.invincible.Filist")
CONFIG = os.path.join(GLib.get_user_config_dir(), "filist")
_OLD_CONFIG = os.path.join(GLib.get_user_config_dir(), "filewise")     # Name bis 2026-10 → einmal umziehen
if not os.path.exists(CONFIG) and os.path.isdir(_OLD_CONFIG):
    try:
        os.rename(_OLD_CONFIG, CONFIG)
    except OSError:
        pass
PREFS = os.path.join(CONFIG, "prefs.json")
try:
    VERSION = open(os.path.join(HERE, "VERSION")).read().strip()
except OSError:
    VERSION = "?"

FW_CSS = """
window.fw label { font-size: 13px; }
.fw-nav { padding: 0 14px 6px 14px; }
button.fw-navbtn { font-size: 16px; padding: 0 6px; min-width: 22px; }
button.fw-navbtn:disabled { color: alpha(@foreground, 0.25); background: none; }
.fw-path { min-height: 26px; }
.fw-crumbs { border: 1px solid alpha(@primary, 0.35); border-radius: 4px; padding: 0 4px; min-height: 24px; }
button.fw-crumb { background: none; border: none; box-shadow: none; padding: 1px 6px; min-height: 0; border-radius: 4px;
    color: alpha(@foreground, 0.75); font-weight: bold; }
button.fw-crumb:hover { background-color: alpha(@primary, 0.16); color: @foreground; }
button.fw-crumb.last { color: @primary; }
.fw-sep { color: alpha(@foreground, 0.3); }
button.fw-viewbtn { font-size: 15px; padding: 1px 7px; }
.fw-body { margin: 0 14px; }
.fw-body > separator { background: none; min-width: 8px; }
.fw-panebody { padding: 2px 4px 4px 4px; }
.fw-places { padding: 2px 0 6px 0; }
window.fw .fw-place-head { color: @secondary; font-size: 11px; margin: 8px 6px 2px 6px; }
button.fw-place { background: none; border: none; box-shadow: none; border-radius: 4px; padding: 2px 6px; min-height: 0;
    color: alpha(@foreground, 0.85); }
button.fw-place:hover { background-color: alpha(@primary, 0.12); color: @foreground; }
button.fw-place.active { background-color: alpha(@primary, 0.22); color: @foreground; }
button.fw-place.drop { background-color: alpha(@primary, 0.3); }
window.fw .pglyph { color: @primary; font-size: 15px; min-width: 18px; }
.fw-freq { margin-left: 14px; }
button.fw-freq-item { padding: 1px 6px; }
window.fw button.fw-freq-item label { font-size: 12px; }
window.fw button.fw-freq-item .pglyph { font-size: 13px; color: alpha(@primary, 0.8); }
window.fw .fw-freq-count, window.fw .fw-freq-chev { color: alpha(@foreground, 0.45); font-size: 11px; }
window.fw .fw-freq-empty { color: alpha(@foreground, 0.45); font-size: 11px; margin: 2px 8px; }
button.fw-eject { background: none; border: none; box-shadow: none; padding: 0 3px; min-height: 0; color: @primary; }
button.fw-eject:hover { background-color: alpha(@primary, 0.2); border-radius: 4px; }
.fw-places levelbar { min-height: 4px; min-width: 34px; }
.fw-places levelbar trough { min-height: 4px; border-radius: 2px; background-color: alpha(@foreground, 0.14); border: none; }
.fw-places levelbar block.filled { background-color: @primary; border-radius: 2px; border: none; min-height: 4px; }
.fw-places levelbar.full block.filled { background-color: @tertiary; }
.fw-places levelbar block.empty { background: none; border: none; }
/* Liste */
columnview.fw-list { background: none; }
columnview.fw-list > header { background: none; border-bottom: 1px dashed alpha(@primary, 0.25); }
columnview.fw-list > header > button { background: none; border: none; box-shadow: none; padding: 2px 6px;
    color: @secondary; font-size: 12px; font-weight: bold; }
columnview.fw-list > header > button:hover { color: @foreground; }
columnview.fw-list > header > button sort-indicator { color: @primary; min-width: 12px; }
columnview.fw-list > listview { background: none; }
columnview.fw-list > listview > row { border-radius: 4px; padding: 0; margin: 0 2px; min-height: 24px; outline: none; }
columnview.fw-list > listview > row:hover { background-color: alpha(@primary, 0.08); }
columnview.fw-list > listview > row:selected { background-color: alpha(@primary, 0.24); color: @foreground; }
columnview.fw-list > listview > row:selected:focus-within { background-color: alpha(@primary, 0.3); }
columnview.fw-list > listview > row > cell { padding: 2px 6px; }
window.fw .fglyph { font-size: 15px; min-width: 18px; color: @secondary; }
/* Dateinamen immer in der Vordergrundfarbe des Themes (GTK-Standard wäre dunkel → auf dunklem Grund unlesbar) */
window.fw columnview.fw-list > listview > row,
window.fw gridview.fw-grid > child { color: @foreground; }
window.fw .fname { font-weight: bold; color: @foreground; }
window.fw .fhidden { color: alpha(@foreground, 0.55); }
/* Rechtsklick/Drücken: kein heller Standard-Effekt (blieb weiß, bis die Maus sich bewegte) */
columnview.fw-list > listview > row:active { background-color: alpha(@primary, 0.24); }
gridview.fw-grid > child:active { background-color: alpha(@primary, 0.24); }
columnview.fw-list > listview > row:selected:hover { background-color: alpha(@primary, 0.3); }
gridview.fw-grid > child:selected:hover { background-color: alpha(@primary, 0.3); }
.flink { font-style: italic; }
window.fw .fmeta { color: @secondary; font-size: 12px; font-weight: normal; }
row:selected .fmeta { color: alpha(@foreground, 0.85); }
.g-dir { color: @primary; }
.g-img { color: @tertiary; } .g-vid { color: @tertiary; } .g-aud { color: @secondary; }
.g-doc { color: @secondary; } .g-txt { color: alpha(@foreground, 0.75); } .g-arc { color: @tertiary; }
.g-code { color: @primary; } .g-exe { color: @primary; } .g-font { color: @secondary; } .g-key { color: @tertiary; }
.g-link { color: @secondary; } .g-file { color: alpha(@foreground, 0.6); }
/* Raster */
gridview.fw-grid { background: none; padding: 4px; }
gridview.fw-grid > child { border-radius: 6px; padding: 6px; outline: none; }
gridview.fw-grid > child:hover { background-color: alpha(@primary, 0.08); }
gridview.fw-grid > child:selected { background-color: alpha(@primary, 0.24); }
window.fw .ftile .fname { font-size: 12px; }
window.fw .tglyph { font-size: 44px; }
window.fw gridview.z0 .tglyph { font-size: 28px; } window.fw gridview.z1 .tglyph { font-size: 36px; }
window.fw gridview.z3 .tglyph { font-size: 60px; } window.fw gridview.z4 .tglyph { font-size: 84px; }
.fthumb { border-radius: 4px; }
window.fw .fw-empty { color: @secondary; font-size: 14px; }
/* Wischen zurück/vor: Pfeil-Blase am Rand */
/* Vorschau-Leiste: Griff zum Größe-Ändern */
.fw-prevsplit > separator { background: none; min-width: 8px; }
.fw-prevsplit > separator:hover { background-color: alpha(@primary, 0.25); border-radius: 4px; }
/* Rechtsklick-Menü: große Symbole oben */
.fw-quick { padding: 2px 2px 4px 2px; }
popover.fw-menu button.fw-qbtn { background: none; border: 1px solid alpha(@primary, 0.35); box-shadow: none;
    border-radius: 6px; padding: 8px 0; min-height: 0; }
window.fw popover.fw-menu button.fw-qbtn label, popover.fw-menu button.fw-qbtn label { font-size: 24px; color: @primary; }
popover.fw-menu button.fw-qbtn:hover { background-color: @primary; border-color: @primary; }
popover.fw-menu button.fw-qbtn:hover label { color: @on_primary; }
popover.fw-menu button.fw-qbtn.danger { border-color: alpha(@tertiary, 0.45); }
popover.fw-menu button.fw-qbtn.danger label { color: @tertiary; }
popover.fw-menu button.fw-qbtn.danger:hover { background-color: @tertiary; border-color: @tertiary; }
popover.fw-menu button.fw-qbtn.danger:hover label { color: @background; }
/* Vorschau, Aufgaben, Dialoge */
.fw-preview { padding: 6px 4px; }
window.fw .fw-prev-glyph { font-size: 72px; color: @primary; margin: 18px 0; }
window.fw .fw-prev-name { font-size: 14px; }
.fw-prev-pic { border-radius: 4px; }
textview.fw-prev-text, textview.fw-prev-text text { background: none; color: @foreground; font-size: 11px; }
.fw-jobspanel { margin: 4px 14px 0 14px; }
.fw-choose { padding: 6px 14px 2px 14px; }
button.fw-tabx { background: none; border: none; box-shadow: none; padding: 0 3px; min-height: 0; min-width: 0;
    color: alpha(@foreground, 0.4); font-size: 11px; border-radius: 4px; }
button.fw-tabx:hover { background-color: @tertiary; color: @background; }
window.fw-dialog { background-color: @background; }
window.fw-dialog.glass { background-color: alpha(@background, 0.6); }
.fw-dhead { padding: 10px 12px 4px 14px; }
.fw-dtitle { color: @primary; }
.fw-dbody { padding: 6px 14px 4px 14px; }
.fw-dbuttons { padding: 8px 14px 14px 14px; }
.fw-dialog spinbutton { border: 1px solid alpha(@primary, 0.45); border-radius: 4px; background: none; min-height: 22px; }
list.fw-applist { background: none; }
list.fw-applist > row { border-radius: 4px; padding: 3px 6px; }
list.fw-applist > row:selected { background-color: alpha(@primary, 0.24); color: @foreground; }
popover.fw-menu > contents { background-color: alpha(@background, 0.94); border: 1px solid @primary; border-radius: 6px;
    padding: 4px; box-shadow: 0 8px 22px rgba(0,0,0,0.3); }
popover.fw-menu modelbutton { border-radius: 4px; padding: 3px 10px; min-height: 0; color: @foreground; }
popover.fw-menu modelbutton:hover { background-color: @primary; color: @on_primary; }
popover.fw-menu separator { background-color: alpha(@primary, 0.25); margin: 3px 4px; min-height: 1px; }
popover.fw-menu arrow { color: @primary; }
/* Markierter Text (Eingabefelder, Vorschau, Dialoge): helle Schrift auf kräftiger Akzentfarbe –
   GTKs Standard war dunkle Schrift auf dunklem Blau und kaum lesbar */
window.fw selection, window.fw-dialog selection, popover selection,
window.fw text selection, window.fw label selection, window.fw textview text selection {
    background-color: @primary; color: @on_primary; }
/* Umbenennen direkt in der Zeile */
window.fw text.fw-inline { background-color: alpha(@background, 0.92); border: 1px solid @primary; border-radius: 4px;
    padding: 1px 5px; margin: -2px 0; color: @foreground; caret-color: @primary; font-weight: bold; }
window.fw text.fw-inline.tile { font-size: 12px; margin: 0; }
/* Laden / VPN verbinden */
.fw-busy spinner { color: @primary; }
.fw-place spinner { color: @primary; min-width: 12px; min-height: 12px; }
"""


class Filist(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE |
                         Gio.ApplicationFlags.HANDLES_OPEN)
        self.prefs = self.load_prefs()
        self.ops = opsmod.Ops()
        self.cut = set()
        self.services = None
        self.held = False

    def load_prefs(self):
        try:
            with open(PREFS) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_prefs(self):
        try:
            os.makedirs(CONFIG, exist_ok=True)
            tmp = PREFS + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.prefs, f, indent=1)
            os.replace(tmp, PREFS)
        except OSError:
            pass

    def do_startup(self):
        Gtk.Application.do_startup(self)
        tui.CSS = tui.CSS + FW_CSS + __import__("swipe").CSS  # eigener Stil muss in denselben Provider (wegen @primary …)
        tui.Theme.get()
        import dialogs
        self.ops.on_conflict = lambda job, kind, src, dst, reply: dialogs.conflict(self._active(), kind, src, dst,
                                                                                    reply)
        self.ops.on_error = lambda job, msg: self._active() and self._active().say(msg, error=True)
        if not os.environ.get("FILIST_NO_SERVICES"):
            import portal
            self.services = portal.Services(self)

    def _active(self):
        w = self.get_active_window()
        if w is None:
            ws = self.get_windows()
            w = ws[0] if ws else None
        return w

    def all_panes(self):
        for w in self.get_windows():
            for t in getattr(w, "tabs", []):
                yield from t.panes

    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        cwd = cmd.get_cwd() or os.path.expanduser("~")
        if "--daemon" in args:
            if not self.held:
                self.hold()
                self.held = True
            return 0
        if "--check" in args:
            cmd.print_literal(check())
            return 0
        targets = [a for a in args if not a.startswith("--")]
        if not targets:
            self.open_window(None)
            return 0
        for t in targets:
            if "://" in t or t.startswith(("trash:", "recent:")):
                f = Gio.File.new_for_uri(t)
            else:
                f = Gio.File.new_for_path(os.path.join(cwd, os.path.expanduser(t)))
            if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY or "://" in t:
                self.open_window(f.get_uri())
            else:
                parent = f.get_parent()
                self.open_window(parent.get_uri() if parent else None, select=[f.get_uri()])
        return 0

    def do_activate(self):
        self.open_window(None)

    def do_open(self, files, n, hint):
        """org.freedesktop.Application.Open (schneller Start über gdbus, siehe Starter-Skript)."""
        for f in files:
            if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY or not f.get_path():
                self.open_window(f.get_uri())
            else:
                parent = f.get_parent()
                self.open_window(parent.get_uri() if parent else None, select=[f.get_uri()])

    def open_window(self, uri, select=None):
        import window
        win = window.FwWindow(self, uri)
        if select:
            win.pane.select_after = select[0]
            orig = win.pane.on_loaded

            def once(pane, orig=orig):
                pane.select_uris(select)
                pane.on_loaded = orig
                if orig:
                    orig(pane)
            win.pane.on_loaded = once
        win.present()
        return win

    def open_chooser(self, mode, chooser, start_uri):
        import window
        win = window.FwWindow(self, start_uri, mode=mode, chooser=chooser)
        title = chooser.get("title") or {"open": "datei öffnen", "save": "speichern unter", "dir": "ordner wählen"}[mode]
        if chooser.get("directory") and mode == "open":
            title = chooser.get("title") or "ordner wählen"
        win.set_title(f"filist · {title}")
        app = chooser.get("app_id") or ""
        win.mode_label.set_label(f"{title.lower()}" + (f"  ·  {app.split('.')[-1].lower()}" if app else ""))
        win.apply_chooser_filter()
        parent = chooser.get("parent") or ""

        def mapped(*_):
            if parent.startswith("wayland:"):
                try:
                    gi.require_version("GdkWayland", "4.0")
                    from gi.repository import GdkWayland
                    surf = win.get_surface()
                    if isinstance(surf, GdkWayland.WaylandToplevel):
                        surf.set_transient_for_exported(parent[len("wayland:"):])
                except (ValueError, ImportError, AttributeError, TypeError):
                    pass
        win.connect("map", mapped)
        win.present()
        if mode == "save" and win.name_entry:
            def focus():
                win.name_entry.grab_focus()
                name = win.name_entry.get_text()
                dot = name.rfind(".")
                win.name_entry.select_region(0, dot if dot > 0 else -1)
                return False
            GLib.timeout_add(150, focus)
        return win


def check():
    out = [f"filist {VERSION}"]

    def ok(v):
        return "ok" if v else "fehlt"
    import shutil
    out.append(f"  GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()} ........ ok")
    try:
        Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                       "org.freedesktop.thumbnails.Thumbnailer1",
                                       "/org/freedesktop/thumbnails/Thumbnailer1",
                                       "org.freedesktop.thumbnails.Thumbnailer1", None)
        import glob
        tumbler = shutil.which("tumblerd") or glob.glob("/usr/lib*/tumbler-1/tumblerd") or \
            glob.glob("/usr/lib/*-linux-gnu/tumbler-1/tumblerd") or glob.glob("/usr/libexec/tumbler-1/tumblerd")
    except GLib.Error:
        tumbler = False
    out.append(f"  vorschaubilder (tumbler) ... {ok(tumbler)}")
    out.append(f"  entpacken (bsdtar/7z) ...... {ok(shutil.which('bsdtar') or shutil.which('7z'))}")
    out.append(f"  netzwerk (gvfs) ............ {ok(any(os.path.exists(p) for p in ('/usr/lib/gvfsd', '/usr/libexec/gvfsd', '/usr/lib/gvfs/gvfsd')) or shutil.which('gvfsd'))}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(Filist().run(sys.argv))
