# Seitenleiste „orte“: Standardordner, Lesezeichen (gleiche Datei wie GTK/Nautilus/Thunar), Laufwerke mit
# Füllstand (einhängen/auswerfen), Papierkorb. Dateien lassen sich auf jeden Eintrag ziehen.
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

import icons
import model

BOOKMARKS = os.path.join(GLib.get_user_config_dir(), "gtk-3.0", "bookmarks")
XDG_DIRS = [
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "\U000F01C4"),
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "\U000F0C82"),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "\U000F024D"),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "\U000F024F"),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "\U000F1359"),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "\U000F19FA"),
]


def read_bookmarks():
    out = []
    try:
        for line in open(BOOKMARKS, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            uri, _, name = line.partition(" ")
            out.append((uri, name or None))
    except OSError:
        pass
    return out


def write_bookmarks(items):
    os.makedirs(os.path.dirname(BOOKMARKS), exist_ok=True)
    tmp = BOOKMARKS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for uri, name in items:
            f.write(uri + (f" {name}" if name else "") + "\n")
    os.replace(tmp, BOOKMARKS)


def add_bookmark(uri):
    items = read_bookmarks()
    if uri not in [u for u, _ in items]:
        items.append((uri, None))
        write_bookmarks(items)
        return True
    return False


def remove_bookmark(uri):
    write_bookmarks([(u, n) for u, n in read_bookmarks() if u != uri])


# ---------- häufig geöffnet ----------
# Quelle: GTKs Liste zuletzt benutzter Dateien (~/.local/share/recently-used.xbel). Jede App, die eine Datei
# öffnet, zählt dort pro Datei mit – filist auch. Häufigkeit × Aktualität („frecency“) ergibt die Reihenfolge.
FREQ_MAX = 8
PICKER_APPS = ("xdg-desktop-portal-gtk", "xdg-desktop-portal-gnome", "xdg-desktop-portal")   # Dateiauswahl zählt nur ein Drittel


def _recency(days):
    for limit, w in ((4, 1.0), (14, 0.7), (31, 0.5), (90, 0.3)):
        if days <= limit:
            return w
    return 0.1


def frequent_files(hidden=(), limit=FREQ_MAX):
    """[(uri, name, anzahl, score)] der am häufigsten geöffneten Dateien (keine Ordner), bestes zuerst."""
    out = []
    try:
        items = Gtk.RecentManager.get_default().get_items()
    except Exception:
        return out
    for info in items:
        uri = info.get_uri()
        if uri in hidden or info.get_mime_type() == "inode/directory":
            continue
        raw, weighted = 0, 0.0
        for app in info.get_applications():
            try:
                cnt = info.get_application_info(app)[1]
            except (TypeError, IndexError):
                continue
            raw += cnt
            weighted += cnt * (0.35 if app in PICKER_APPS else 1.0)
        if raw < 2:
            continue
        if uri.startswith("file:"):
            path = Gio.File.new_for_uri(uri).get_path()
            if not path or "/gvfs/" in path:
                pass                                # Netz über FUSE: nicht anfassen (stat kann Sekunden dauern)
            elif not os.path.isfile(path):
                continue
        out.append((uri, info.get_display_name() or uri.rsplit("/", 1)[-1], raw,
                    weighted * _recency(info.get_age())))
    out.sort(key=lambda r: -r[3])
    return out[:limit]


class UsageBar(Gtk.DrawingArea):
    """Schmaler Füllstandsbalken (Laufwerk) – gezeichnet, damit er wirklich nur 4 px hoch ist."""

    def __init__(self, used):
        super().__init__(content_width=34, content_height=4, valign=Gtk.Align.CENTER)
        self.used = used
        self.set_draw_func(self._draw)

    def _draw(self, area, cr, w, h):
        import tui
        c = tui.colors()
        bg, fg = c.get("foreground"), c.get("tertiary" if self.used > 0.9 else "primary")
        if bg is None or fg is None:
            return
        cr.set_source_rgba(bg.red, bg.green, bg.blue, 0.15)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 1)
        cr.rectangle(0, 0, max(2, w * self.used), h)
        cr.fill()


class Places(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.add_css_class("fw-places")
        self.win = win
        self.rows = []              # (button, uri)
        self.vm = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed", "volume-added", "volume-removed",
                    "volume-changed"):
            self.vm.connect(sig, lambda *_: self.queue_rebuild())
        self._rebuild_id = None
        self._freq_id = None
        self.freq_box = None
        acts = Gio.SimpleActionGroup()
        for name, cb in (("open", self.freq_open), ("show", self.freq_show), ("hide", self.freq_hide)):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v, cb=cb: cb(v.get_string()))
            acts.add_action(a)
        self.insert_action_group("freq", acts)
        # globales Objekt → nur verbinden, solange das Fenster lebt (sonst hält es die Seitenleiste fest)
        self._recent_hid = None
        self.connect("realize", self._recent_watch, True)
        self.connect("unrealize", self._recent_watch, False)
        try:
            mon = Gio.File.new_for_path(BOOKMARKS).monitor_file(Gio.FileMonitorFlags.NONE, None)
            mon.connect("changed", lambda *_: self.queue_rebuild())
            self._bm_mon = mon
        except GLib.Error:
            pass
        self.rebuild()

    def queue_rebuild(self):
        if self._rebuild_id is None:
            self._rebuild_id = GLib.timeout_add(150, self._do_rebuild)

    def _do_rebuild(self):
        self._rebuild_id = None
        self.rebuild()
        return False

    def rebuild(self):
        while (c := self.get_first_child()):
            self.remove(c)
        self.rows = []
        home = os.path.expanduser("~")
        self._head("orte")
        self._row(icons.HOME, "home", model.uri_for(home))
        fallback = {GLib.UserDirectory.DIRECTORY_DESKTOP: ("Desktop", "Schreibtisch"),
                    GLib.UserDirectory.DIRECTORY_DOCUMENTS: ("Documents", "Dokumente"),
                    GLib.UserDirectory.DIRECTORY_DOWNLOAD: ("Downloads",),
                    GLib.UserDirectory.DIRECTORY_PICTURES: ("Pictures", "Bilder"),
                    GLib.UserDirectory.DIRECTORY_MUSIC: ("Music", "Musik"),
                    GLib.UserDirectory.DIRECTORY_VIDEOS: ("Videos",)}
        for d, glyph in XDG_DIRS:
            p = GLib.get_user_special_dir(d)
            if not p or not os.path.isdir(p):       # ohne user-dirs.dirs: übliche Namen probieren
                p = next((os.path.join(home, n) for n in fallback.get(d, ()) if os.path.isdir(os.path.join(home, n))),
                         None)
            if p and os.path.isdir(p) and os.path.realpath(p) != os.path.realpath(home):
                self._row(glyph, os.path.basename(p).lower(), model.uri_for(p))
        for extra in ("Projects", "Projekte"):
            p = os.path.join(home, extra)
            if os.path.isdir(p):
                self._row("\U000F19FC", extra.lower(), model.uri_for(p))
        self._row(icons.RECENT, "zuletzt benutzt", "recent:///")
        self._freq_section()

        bms = read_bookmarks()
        if bms:
            self._head("lesezeichen")
            for uri, name in bms:
                f = Gio.File.new_for_uri(uri)
                label = name or (f.get_basename() if f.get_path() else uri.split("://", 1)[-1])
                glyph = icons.NETWORK if not uri.startswith("file:") else icons.BOOKMARK
                self._row(glyph, label, uri, bookmark=True)

        self._head("laufwerke")
        self._row(icons.ROOT, "system", "file:///", usage_path="/")
        seen = set()
        for mount in self.vm.get_mounts():
            root = mount.get_root()
            if root.get_path() in ("/", None) and root.get_uri().startswith("file:"):
                continue
            if mount.is_shadowed():
                continue
            seen.add(mount.get_volume() and mount.get_volume().get_uuid())
            glyph = icons.USB if mount.can_eject() else (icons.NETWORK if not root.get_path() else icons.DRIVE)
            self._row(glyph, mount.get_name().lower(), root.get_uri(), usage_path=root.get_path(), mount=mount)
        for vol in self.vm.get_volumes():
            if vol.get_mount() is None and vol.can_mount():
                self._row(icons.DRIVE, vol.get_name().lower(), None, volume=vol)

        self._head("sonstiges")
        self._row(icons.TRASH, "papierkorb", "trash:///", trash=True)
        self._row(icons.NETWORK, "netzwerk …", "network:///", connect=True)
        self.highlight(self.win.current_uri() if hasattr(self.win, "current_uri") else None)

    # ----- häufig geöffnet (aufklappbar unter „zuletzt benutzt“) -----
    def _freq_section(self):
        prefs = getattr(self.win, "prefs", {})
        open_ = prefs.get("freq_open", True)
        t = Gtk.Button(focus_on_click=False, tooltip_text="Dateien, die du oft öffnest (aus allen Apps)")
        t.add_css_class("fw-place")
        t.add_css_class("fw-freq-toggle")
        box = Gtk.Box(spacing=8)
        g = Gtk.Label(label="\U000F04CE", xalign=0.5)      # Stern
        g.add_css_class("pglyph")
        n = Gtk.Label(label="häufig geöffnet", xalign=0, hexpand=True)
        self.freq_chev = Gtk.Label(label="\U000F0140" if open_ else "\U000F0142")
        self.freq_chev.add_css_class("fw-freq-chev")
        for w in (g, n, self.freq_chev):
            box.append(w)
        t.set_child(box)
        self.freq_rev = Gtk.Revealer(reveal_child=open_, transition_duration=150,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.freq_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.freq_box.add_css_class("fw-freq")
        self.freq_rev.set_child(self.freq_box)
        t.connect("clicked", lambda *_: self.freq_toggle())
        self.append(t)
        self.append(self.freq_rev)
        self.fill_freq()

    def _recent_watch(self, _w, on):
        rm = Gtk.RecentManager.get_default()
        if on and self._recent_hid is None:
            self._recent_hid = rm.connect("changed", lambda *_: self.queue_freq())
        elif not on and self._recent_hid is not None:
            rm.disconnect(self._recent_hid)
            self._recent_hid = None
            if self._freq_id is not None:
                GLib.source_remove(self._freq_id)
                self._freq_id = None

    def freq_toggle(self):
        on = not self.freq_rev.get_reveal_child()
        self.freq_rev.set_reveal_child(on)
        self.freq_chev.set_label("\U000F0140" if on else "\U000F0142")
        prefs = getattr(self.win, "prefs", None)
        if prefs is not None:
            prefs["freq_open"] = on
            app = self.win.get_application() if hasattr(self.win, "get_application") else None
            if app is not None and hasattr(app, "save_prefs"):
                app.save_prefs()

    def queue_freq(self):
        if self._freq_id is None:
            self._freq_id = GLib.timeout_add(400, self._do_freq)

    def _do_freq(self):
        self._freq_id = None
        self.fill_freq()
        return False

    def fill_freq(self):
        box = self.freq_box
        if box is None:
            return
        while (c := box.get_first_child()):
            box.remove(c)
        hidden = set(getattr(self.win, "prefs", {}).get("freq_hidden", []))
        files = frequent_files(hidden)
        if not files:
            l = Gtk.Label(label="noch nichts – öffne dateien öfter", xalign=0)
            l.add_css_class("fw-freq-empty")
            box.append(l)
            return
        for uri, name, cnt, _score in files:
            b = Gtk.Button(focus_on_click=False)
            b.add_css_class("fw-place")
            b.add_css_class("fw-freq-item")
            glyph, _grp = icons.for_name(name, False)
            g = Gtk.Label(label=glyph, xalign=0.5)
            g.add_css_class("pglyph")
            n = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
            c = Gtk.Label(label=f"{cnt}×")
            c.add_css_class("fw-freq-count")
            row = Gtk.Box(spacing=8)
            for w in (g, n, c):
                row.append(w)
            b.set_child(row)
            f = Gio.File.new_for_uri(uri)
            b.set_tooltip_text(f"{f.get_parse_name()}\n{cnt}× geöffnet · Klick öffnet, Mittelklick zeigt den Ordner")
            b.connect("clicked", lambda *_, u=uri: self.freq_open(u))
            mid = Gtk.GestureClick(button=2)
            mid.connect("pressed", lambda *_, u=uri: self.freq_show(u, tab=True))
            b.add_controller(mid)
            right = Gtk.GestureClick(button=3)
            right.connect("pressed", lambda g_, n_, x, y, b=b, u=uri: self._freq_menu(b, u, x, y))
            b.add_controller(right)
            drag = Gtk.DragSource(actions=Gdk.DragAction.COPY)
            drag.connect("prepare", lambda *_, u=uri: Gdk.ContentProvider.new_for_value(
                Gdk.FileList.new_from_list([Gio.File.new_for_uri(u)])))
            b.add_controller(drag)
            box.append(b)

    def _freq_menu(self, b, uri, x, y):
        menu = Gio.Menu()
        v = GLib.Variant.new_string(uri).print_(False)
        menu.append("öffnen", f"freq.open({v})")
        menu.append("im ordner zeigen", f"freq.show({v})")
        menu.append("aus der liste nehmen", f"freq.hide({v})")
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.add_css_class("fw-menu")
        pop.set_has_arrow(False)
        pop.set_parent(b)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    def freq_open(self, uri):
        if getattr(self.win, "mode", "browse") != "browse":   # Dateiauswahl: hinspringen und markieren
            self.freq_show(uri)
            return
        ctx = self.get_display().get_app_launch_context()

        def done(_src, res):
            try:
                Gio.AppInfo.launch_default_for_uri_finish(res)
                Gtk.RecentManager.get_default().add_item(uri)          # zählt mit
            except GLib.Error as e:
                if hasattr(self.win, "say"):
                    self.win.say(f"öffnen fehlgeschlagen: {e.message}", error=True)
        Gio.AppInfo.launch_default_for_uri_async(uri, ctx, None, done)
        if hasattr(self.win, "say"):
            self.win.say(f"öffne {Gio.File.new_for_uri(uri).get_basename()}")

    def freq_show(self, uri, tab=False):
        parent = Gio.File.new_for_uri(uri).get_parent()
        if parent is None:
            return
        if tab and hasattr(self.win, "new_tab"):
            self.win.new_tab(parent.get_uri())
            self.win.pane.select_after = uri
            return
        self.win.pane.go(parent.get_uri(), select=uri)

    def freq_hide(self, uri):
        prefs = getattr(self.win, "prefs", None)
        if prefs is None:
            return
        lst = prefs.setdefault("freq_hidden", [])
        if uri not in lst:
            lst.append(uri)
            del lst[:-200]
        app = self.win.get_application()
        if app is not None and hasattr(app, "save_prefs"):
            app.save_prefs()
        self.fill_freq()

    def _head(self, text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("fw-place-head")
        self.append(l)

    def _row(self, glyph, name, uri, bookmark=False, usage_path=None, mount=None, volume=None, trash=False,
             connect=False):
        b = Gtk.Button(focus_on_click=False)
        b.add_css_class("fw-place")
        g = Gtk.Label(label=glyph, xalign=0.5)
        g.add_css_class("pglyph")
        n = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box = Gtk.Box(spacing=8)
        box.append(g)
        box.append(n)
        if usage_path:
            bar = UsageBar(0)
            bar.set_visible(False)
            box.append(bar)

            def show_usage(free, total, bar=bar, b=b):
                if total:
                    bar.used = 1 - free / total
                    bar.set_visible(True)
                    bar.queue_draw()
                    b.set_tooltip_text(f"{model.human_size(free)} frei von {model.human_size(total)}")
            show_usage(*model.disk_free(usage_path, on_done=show_usage))
        if mount is not None and (mount.can_eject() or mount.can_unmount()):
            ej = Gtk.Button(label="\U000F01EA", tooltip_text="Auswerfen", focus_on_click=False)
            ej.add_css_class("fw-eject")
            ej.connect("clicked", lambda *_: self.eject(mount))
            box.append(ej)
        if trash:
            try:
                info = Gio.File.new_for_uri("trash:///").query_info("trash::item-count", 0, None)
                cnt = info.get_attribute_uint32("trash::item-count")
                if cnt:
                    c = Gtk.Label(label=str(cnt))
                    c.add_css_class("dim")
                    box.append(c)
            except GLib.Error:
                pass
        b.set_child(box)
        b._uri = uri
        if volume is not None:
            b.connect("clicked", lambda *_: self.mount_volume(volume))
        elif connect:
            b.connect("clicked", lambda *_: self.win.connect_server())
        else:
            b.connect("clicked", lambda *_: self.win.open_location(uri))
        mid = Gtk.GestureClick(button=2)
        mid.connect("pressed", lambda *_: uri and self.win.new_tab(uri))
        b.add_controller(mid)
        right = Gtk.GestureClick(button=3)
        right.connect("pressed", lambda g, n, x, y: self._menu(b, uri, bookmark, trash, mount, x, y))
        b.add_controller(right)
        if uri and (uri.startswith("file:") or trash):
            drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            drop.connect("drop", lambda t, v, x, y: self.win.drop_files([f.get_uri() for f in v.get_files()], uri,
                                                                       Gdk.DragAction.MOVE if trash else None))
            drop.connect("enter", lambda *_: (b.add_css_class("drop"), Gdk.DragAction.COPY)[1])
            drop.connect("leave", lambda *_: b.remove_css_class("drop"))
            b.add_controller(drop)
        self.append(b)
        self.rows.append((b, uri))

    def busy(self, uri, on):
        """Drehkreis an der Zeile eines Orts, solange VPN/Einhängen läuft."""
        for b, u in self.rows:
            if not u or u.rstrip("/") != (uri or "").rstrip("/"):
                continue
            box, sp = b.get_child(), getattr(b, "_spin", None)
            if on and sp is None and box is not None:
                b._spin = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
                box.append(b._spin)
            elif not on and sp is not None:
                sp.stop()
                box.remove(sp)
                b._spin = None

    def highlight(self, uri):
        for b, u in self.rows:
            (b.add_css_class if u and uri and u == uri else b.remove_css_class)("active")

    def _menu(self, b, uri, bookmark, trash, mount, x, y):
        menu = Gio.Menu()
        if uri:
            menu.append("öffnen", f"win.go::{uri}")
            menu.append("in neuem tab", f"win.go-tab::{uri}")
        if bookmark:
            menu.append("lesezeichen entfernen", f"win.bookmark-remove::{uri}")
        elif uri and uri.startswith("file:"):
            menu.append("als lesezeichen", f"win.bookmark-add::{uri}")
        if trash:
            menu.append("papierkorb leeren", "win.empty-trash")
        if mount is not None:
            menu.append("auswerfen", f"win.eject::{uri}")
        if uri and not uri.startswith(("file:", "trash:", "recent:")):
            import vpn
            conns = vpn.connections()
            if conns:                    # Netzlaufwerk: vorher automatisch ein VPN verbinden?
                cur = (vpn.rule_for(uri, self.win.prefs) or {}).get("nm")
                sub = Gio.Menu()
                sub.append(("● " if not cur else "   ") + "ohne vpn", "win.vpn-set::" + uri + "\n")
                for c in conns:
                    sub.append(("● " if c == cur else "   ") + c, "win.vpn-set::" + uri + "\n" + c)
                menu.append_submenu("vpn vorher verbinden", sub)
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.add_css_class("fw-menu")
        pop.set_has_arrow(False)
        pop.set_parent(b)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    # ----- Laufwerke -----
    def mount_volume(self, vol):
        op = Gtk.MountOperation.new(self.win)

        def done(v, res):
            try:
                v.mount_finish(res)
                m = v.get_mount()
                if m:
                    self.win.open_location(m.get_root().get_uri())
            except GLib.Error as e:
                self.win.say(f"einhängen fehlgeschlagen: {e.message}", error=True)
        vol.mount(Gio.MountMountFlags.NONE, op, None, done)

    def eject(self, mount):
        op = Gtk.MountOperation.new(self.win)
        name = mount.get_name()

        def done(m, res):
            try:
                (m.eject_with_operation_finish if m.can_eject() else m.unmount_with_operation_finish)(res)
                self.win.say(f"„{name}“ kann jetzt entfernt werden")
            except GLib.Error as e:
                self.win.say(f"auswerfen fehlgeschlagen: {e.message}", error=True)
        if mount.can_eject():
            mount.eject_with_operation(Gio.MountUnmountFlags.NONE, op, None, done)
        else:
            mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, op, None, done)

    def eject_uri(self, uri):
        for m in self.vm.get_mounts():
            if m.get_root().get_uri() == uri:
                self.eject(m)
                return
