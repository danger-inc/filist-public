# Dialoge im Stil der Suite: Konflikt, Bestätigen, Umbenennen (einzeln + mehrere), Neu, Eigenschaften,
# Öffnen mit, Packen, Server verbinden. Alle schließen mit Esc, Enter bestätigt.
import grp, hashlib, os, pwd, re, stat, threading, time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

import model
import tui


class Dialog(Gtk.Window):
    def __init__(self, parent, title, width=460):
        super().__init__(transient_for=parent, modal=True, title=title, resizable=False, default_width=width)
        self.add_css_class("pb-window")
        self.add_css_class("tui")
        self.add_css_class("fw-dialog")
        self.set_decorated(False)
        head = tui.hbox(spacing=8)
        head.add_css_class("fw-dhead")
        t = tui.label(title, "fw-dtitle", hexpand=True, ellipsize=True)
        head.append(t)
        close = tui.Button("✕", self.close, tooltip="Schließen (Esc)")
        close.add_css_class("close")
        head.append(close)
        self.body = tui.vbox(spacing=10)
        self.body.add_css_class("fw-dbody")
        self.buttons = tui.hbox(spacing=8, halign=Gtk.Align.END)
        self.buttons.add_css_class("fw-dbuttons")
        root = tui.vbox(Gtk.WindowHandle(child=head), self.body, self.buttons, spacing=0)
        self.set_child(root)
        # CAPTURE: Enter löst immer die Standardaktion aus – nicht den gerade fokussierten Knopf
        k = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        k.connect("key-pressed", self._key)
        self.add_controller(k)
        self.default = None

    def _key(self, _c, kv, _code, state):
        if kv == Gdk.KEY_Escape:
            self.close()
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.default is not None:
            foc = self.get_focus()
            if isinstance(foc, (Gtk.TextView, Gtk.ListBox, Gtk.ListBoxRow)):
                return False
            if isinstance(foc, Gtk.Button) and foc.is_ancestor(self.body):
                return False                 # z. B. Auswahlliste/Check im Inhalt: normal bedienen
            self.default()
            return True
        return False

    def button(self, text, cb, danger=False, default=False):
        b = tui.Button(text, cb, danger=danger)
        if default:
            b.add_css_class("on")
            self.default = cb
        self.buttons.append(b)
        return b


# ---------------------------------------------------------------- Konflikt
def conflict(parent, kind, src, dst, reply):
    """Datei existiert schon. reply(choice, for_all)."""
    d = Dialog(parent, "existiert schon")
    name = dst.get_basename()
    what = "ordner" if kind == "dir" else "datei"
    d.body.append(tui.label(f"Im Ziel gibt es schon einen {what} „{name}“.", wrap=True))

    def info(f, title):
        try:
            i = f.query_info("standard::size,time::modified,standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                             None)
            size = "" if i.get_file_type() == Gio.FileType.DIRECTORY else model.human_size(i.get_size())
            when = model.human_date(i.get_attribute_uint64("time::modified"))
            return tui.hbox(tui.label(title, "dim"), tui.label(f"{size}  {when}".strip(), hexpand=True, xalign=1),
                            spacing=10)
        except GLib.Error:
            return tui.label(title, "dim")
    d.body.append(info(dst, "vorhanden"))
    d.body.append(info(src, "neu"))
    all_check = tui.Check("für alle weiteren konflikte", False)
    d.body.append(all_check)
    answered = {"done": False}

    def pick(c):
        answered["done"] = True
        reply(c, all_check.active)
        d.close()
    d.connect("close-request", lambda *_: (answered["done"] or reply("cancel"), False)[-1])
    d.button("abbrechen", lambda: pick("cancel"))
    d.button("überspringen", lambda: pick("skip"))
    d.button("beide behalten", lambda: pick("rename"))
    if kind == "dir":
        d.button("zusammenführen", lambda: pick("merge"), default=True)
        d.button("ersetzen", lambda: pick("replace"), danger=True)
    else:
        d.button("ersetzen", lambda: pick("replace"), danger=True, default=True)
    d.present()


def confirm(parent, title, text, ok_text, cb, danger=True):
    d = Dialog(parent, title)
    d.body.append(tui.label(text, wrap=True))
    d.button("abbrechen", d.close)
    d.button(ok_text, lambda: (d.close(), cb()), danger=danger, default=True)
    d.present()
    return d


def ask_text(parent, title, label, initial, cb, select_stem=True, ok_text="ok", validate=None):
    d = Dialog(parent, title)
    if label:
        d.body.append(tui.label(label, "dim"))
    e = tui.Entry(text=initial)
    d.body.append(e)
    err = tui.label("", "warn", wrap=True)
    d.body.append(err)

    def ok():
        v = e.get_text().strip()
        msg = validate(v) if validate else (None if v else "Bitte einen Namen eingeben")
        if msg:
            err.set_label(msg)
            return
        d.close()
        cb(v)
    e.connect("activate", lambda *_: ok())
    d.button("abbrechen", d.close)
    d.button(ok_text, ok, default=True)
    d.present()

    def sel():
        e.grab_focus()
        stem = len(initial)
        if select_stem and "." in initial[1:]:
            stem = initial.rfind(".") if not initial.lower().endswith((".tar.gz", ".tar.xz", ".tar.bz2")) else \
                initial.lower().rfind(".tar.")
        e.select_region(0, stem)
        return False
    GLib.idle_add(sel)
    return d


def name_error(name, folder_path=None, current=None):
    if not name:
        return "Bitte einen Namen eingeben"
    if "/" in name or name in (".", ".."):
        return "„/“, „.“ und „..“ sind als Name nicht erlaubt"
    if len(name.encode()) > 255:
        return "Name ist zu lang"
    if folder_path and name != current and os.path.lexists(os.path.join(folder_path, name)):
        return "Diesen Namen gibt es hier schon"
    return None


# ---------------------------------------------------------------- mehrere umbenennen
class BatchRename(Dialog):
    def __init__(self, parent, items, apply):
        super().__init__(parent, f"{len(items)} elemente umbenennen", width=620)
        self.items, self.apply_cb = items, apply
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        self.find = tui.Entry("suchen (leer = ganzer name)")
        self.repl = tui.Entry("ersetzen durch – {n} = nummer, {name} = alter name, {ext} = endung")
        self.regex = tui.Check("regulärer ausdruck", False, lambda _v: self.update())
        self.case = tui.Select(lambda v: (setattr(self, "case_mode", v), self.update()))
        self.case_mode = "keep"
        self.case.set_options([("keep", "groß/klein lassen"), ("lower", "alles klein"), ("upper", "alles groß"),
                               ("title", "Wörter Groß")], "keep")
        self.case.lbl.set_width_chars(17)
        self.start = Gtk.SpinButton.new_with_range(0, 99999, 1)
        self.start.set_value(1)
        self.digits = Gtk.SpinButton.new_with_range(1, 6, 1)
        self.digits.set_value(2)
        self.keep_ext = tui.Check("endung behalten", True, lambda _v: self.update())
        for w in (self.find, self.repl):
            w.connect("changed", lambda *_: self.update())
        for w in (self.start, self.digits):
            w.connect("value-changed", lambda *_: self.update())
        rows = [("suchen", self.find), ("ersetzen", self.repl)]
        for i, (k, w) in enumerate(rows):
            grid.attach(tui.label(k, "dim"), 0, i, 1, 1)
            grid.attach(w, 1, i, 1, 1)
        opts = tui.hbox(self.regex, self.keep_ext, self.case, tui.label("nummer ab", "dim"), self.start,
                        tui.label("stellen", "dim"), self.digits, spacing=10)
        self.body.append(grid)
        self.body.append(opts)
        self.preview = tui.vbox(spacing=2)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=220, max_content_height=360,
                                propagate_natural_height=True, child=self.preview)
        self.body.append(tui.Panel("vorschau", sc))
        self.err = tui.label("", "warn")
        self.body.append(self.err)
        self.button("abbrechen", self.close)
        self.ok = self.button("umbenennen", self.do, default=True)
        self.update()

    def names(self):
        out = []
        find, repl = self.find.get_text(), self.repl.get_text()
        try:
            rx = re.compile(find) if self.regex.active and find else None
        except re.error as e:
            return None, f"Regex: {e}"
        n0, digits = int(self.start.get_value()), int(self.digits.get_value())
        for i, it in enumerate(self.items):
            name = it.name
            stem, ext = (name, "")
            if self.keep_ext.active and not it.is_dir and "." in name[1:]:
                k = name.rfind(".")
                stem, ext = name[:k], name[k:]
            num = str(n0 + i).zfill(digits)
            tmpl = repl.replace("{n}", num).replace("{name}", stem).replace("{ext}", ext.lstrip("."))
            if not find:
                new = tmpl if repl else stem
            elif rx:
                new = rx.sub(tmpl, stem)
            else:
                new = stem.replace(find, tmpl)
            if self.case_mode == "lower":
                new = new.lower()
            elif self.case_mode == "upper":
                new = new.upper()
            elif self.case_mode == "title":
                new = new.title()
            out.append(new + ext)
        return out, None

    def update(self):
        tui.clear(self.preview)
        names, err = self.names()
        if err:
            self.err.set_label(err)
            self.ok.set_sensitive(False)
            return
        dup = {n for n in names if names.count(n) > 1}
        bad = [n for n in names if model and name_error(n)]
        for it, new in zip(self.items, names):
            changed = new != it.name
            row = tui.hbox(tui.label(it.name, "dim", ellipsize=True, hexpand=True),
                           tui.label("→", "accent"),
                           tui.label(new, "warn" if new in dup else ("" if changed else "dim"), ellipsize=True,
                                     hexpand=True), spacing=8)
            self.preview.append(row)
        msg = "Doppelte Namen" if dup else ("Ungültiger Name" if bad else "")
        self.err.set_label(msg)
        self.ok.set_sensitive(not msg)

    def do(self):
        names, err = self.names()
        if err or not self.ok.get_sensitive():
            return
        self.close()
        self.apply_cb([(it, new) for it, new in zip(self.items, names) if new != it.name])


# ---------------------------------------------------------------- Eigenschaften
class Properties(Dialog):
    def __init__(self, parent, items, set_default_app):
        title = items[0].display if len(items) == 1 else f"{len(items)} elemente"
        super().__init__(parent, f"eigenschaften – {title}", width=520)
        self.items = items
        self.cancel = threading.Event()
        self.connect("close-request", lambda *_: (self.cancel.set(), False)[-1])
        it = items[0]
        grid = Gtk.Grid(column_spacing=14, row_spacing=6)
        self.rows = 0

        def row(k, v, mono=False, sel=True):
            l = tui.label(str(v), wrap=True, hexpand=True)
            l.set_selectable(sel)
            l.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            grid.attach(tui.label(k, "dim"), 0, self.rows, 1, 1)
            grid.attach(l, 1, self.rows, 1, 1)
            self.rows += 1
            return l
        if len(items) == 1:
            row("name", it.display)
            row("typ", it.type_text + (f"  ({it.ctype})" if it.ctype and not it.is_dir else ""))
            row("ort", model.path_of(Gio.File.new_for_uri(it.uri).get_parent().get_uri()) or it.uri)
            if it.is_link and it.path:
                try:
                    row("ziel", os.readlink(it.path))
                except OSError:
                    pass
        self.size_lbl = row("größe", "wird berechnet …" if any(i.is_dir for i in items) else
                            model.human_size(sum(i.size or 0 for i in items)))
        if len(items) == 1 and it.path:
            try:
                st = os.lstat(it.path)
                row("geändert", time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(st.st_mtime)))
                row("zugriff", time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(st.st_atime)))
                try:
                    owner = pwd.getpwuid(st.st_uid).pw_name
                    group = grp.getgrgid(st.st_gid).gr_name
                except KeyError:
                    owner, group = st.st_uid, st.st_gid
                row("besitzer", f"{owner} : {group}")
                free, total = model.disk_free(it.path)
                if total:
                    row("laufwerk", f"{model.human_size(free)} frei von {model.human_size(total)}")
            except OSError:
                pass
        self.body.append(tui.Panel("allgemein", grid))

        # Rechte
        paths = [i.path for i in items if i.path]
        if paths:
            try:
                mode = stat.S_IMODE(os.lstat(paths[0]).st_mode)
            except OSError:
                mode = 0
            self.mode = mode
            pg = Gtk.Grid(column_spacing=12, row_spacing=4)
            self.checks = {}
            for c, (who, shift) in enumerate((("besitzer", 6), ("gruppe", 3), ("andere", 0))):
                pg.attach(tui.label(who, "dim"), c + 1, 0, 1, 1)
                for r, (what, bit) in enumerate((("lesen", 4), ("schreiben", 2), ("ausführen", 1))):
                    if c == 0:
                        pg.attach(tui.label(what if not it.is_dir or bit != 1 else "öffnen", "dim"), 0, r + 1, 1, 1)
                    chk = tui.Check("", bool(mode & (bit << shift)), lambda _v: self.update_mode())
                    self.checks[(shift, bit)] = chk
                    pg.attach(chk, c + 1, r + 1, 1, 1)
            self.octal = tui.label(f"{mode:04o}", "accent")
            pg.attach(self.octal, 4, 1, 1, 1)
            self.recursive = tui.Check("auch für alles darin", False) if any(i.is_dir for i in items) else None
            box = tui.vbox(pg, spacing=6)
            if self.recursive:
                box.append(self.recursive)
            box.append(tui.hbox(tui.Button("rechte übernehmen", self.apply_mode), spacing=6))
            self.body.append(tui.Panel("rechte", box))

        # Öffnen mit (eine Datei)
        if len(items) == 1 and not it.is_dir and it.ctype:
            apps = Gio.AppInfo.get_all_for_type(it.ctype)
            default = Gio.AppInfo.get_default_for_type(it.ctype, False)
            if apps:
                sel = tui.Select(lambda app_id: set_default_app(it.ctype, app_id), max_chars=34)
                sel.set_options([(a.get_id(), a.get_display_name()) for a in apps],
                                default.get_id() if default else None)
                self.body.append(tui.Panel("standard-programm", tui.hbox(tui.label(f"für {it.type_text}", "dim",
                                                                                     hexpand=True), sel)))
            # Prüfsumme
            self.sum_lbl = tui.label("", wrap=True)
            self.sum_lbl.set_selectable(True)
            self.sum_lbl.set_wrap_mode(Pango.WrapMode.CHAR)
            sums = tui.hbox(tui.Button("md5", lambda: self.checksum("md5")),
                            tui.Button("sha1", lambda: self.checksum("sha1")),
                            tui.Button("sha256", lambda: self.checksum("sha256")), spacing=6)
            self.body.append(tui.Panel("prüfsumme", tui.vbox(sums, self.sum_lbl, spacing=6)))
        self.err = tui.label("", "warn", wrap=True)
        self.body.append(self.err)
        self.button("schließen", self.close, default=True)
        threading.Thread(target=self._measure, daemon=True).start()
        self.connect("map", lambda *_: GLib.idle_add(lambda: self.set_focus(None) and False))

    def _measure(self):
        total, files, dirs = 0, 0, 0
        last = time.monotonic()
        for it in self.items:
            if it.path and it.is_dir and not it.is_link:
                for root, ds, fs in os.walk(it.path):
                    if self.cancel.is_set():
                        return
                    dirs += len(ds)
                    for f in fs:
                        try:
                            total += os.lstat(os.path.join(root, f)).st_size
                        except OSError:
                            pass
                    files += len(fs)
                    if time.monotonic() - last > 0.2:
                        last = time.monotonic()
                        t, f_, d_ = total, files, dirs
                        GLib.idle_add(lambda t=t, f_=f_, d_=d_: self.size_lbl.set_label(
                            f"{model.human_size(t)} … ({f_} dateien, {d_} ordner)") and False)
            else:
                total += it.size or 0
                files += 1
        txt = f"{model.human_size(total)}  ({total:,} bytes)".replace(",", ".")
        if dirs or any(i.is_dir for i in self.items):
            txt += f" · {files} dateien, {dirs} ordner"
        GLib.idle_add(lambda: self.size_lbl.set_label(txt) and False)

    def update_mode(self):
        m = 0
        for (shift, bit), chk in self.checks.items():
            if chk.active:
                m |= bit << shift
        self.mode = (self.mode & ~0o777) | m
        self.octal.set_label(f"{self.mode:04o}")

    def apply_mode(self):
        errors = []
        rec = self.recursive.active if self.recursive else False
        for it in self.items:
            if not it.path:
                continue
            try:
                os.chmod(it.path, self.mode)
                if rec and it.is_dir:
                    for root, ds, fs in os.walk(it.path):
                        for name in ds + fs:
                            p = os.path.join(root, name)
                            if os.path.islink(p):
                                continue
                            m = self.mode
                            if not os.path.isdir(p):          # Dateien bekommen kein „ausführen“ geschenkt
                                m = m & ~0o111 | (os.lstat(p).st_mode & 0o111 & m)
                            os.chmod(p, m)
            except OSError as e:
                errors.append(f"{it.display}: {e.strerror}")
        self.err.set_label("\n".join(errors[:5]) if errors else "")
        if not errors:
            self.err.set_label("▸ rechte übernommen")

    def checksum(self, algo):
        it = self.items[0]
        self.sum_lbl.set_label(f"{algo} wird berechnet …")

        def work():
            h = hashlib.new(algo)
            try:
                with open(it.path, "rb") as f:
                    while chunk := f.read(1 << 20):
                        if self.cancel.is_set():
                            return
                        h.update(chunk)
                res = f"{algo}: {h.hexdigest()}"
            except OSError as e:
                res = f"Fehler: {e.strerror}"
            GLib.idle_add(lambda: self.sum_lbl.set_label(res) and False)
        threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- Öffnen mit
class OpenWith(Dialog):
    def __init__(self, parent, items, launch, set_default):
        it = items[0]
        super().__init__(parent, f"öffnen mit – {it.display if len(items) == 1 else f'{len(items)} dateien'}",
                         width=480)
        self.items, self.launch, self.set_default_cb = items, launch, set_default
        rec = Gio.AppInfo.get_recommended_for_type(it.ctype) if it.ctype else []
        rec_ids = {a.get_id() for a in rec}
        others = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id() not in rec_ids]
        others.sort(key=lambda a: a.get_display_name().casefold())
        self.apps = [(a, True) for a in rec] + [(a, False) for a in others]
        self.search = tui.Entry("programm suchen …")
        self.search.connect("changed", lambda *_: self.fill())
        self.body.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("fw-applist")
        self.list.connect("row-activated", lambda lb, r: self.do())
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=320, child=self.list)
        self.body.append(sc)
        self.make_default = tui.Check(f"immer damit öffnen ({it.type_text})", False)
        if it.ctype and not it.is_dir:
            self.body.append(self.make_default)
        self.cmd = tui.Entry("oder befehl eingeben, z. B. gimp %f")
        self.body.append(self.cmd)
        self.button("abbrechen", self.close)
        self.button("öffnen", self.do, default=True)
        self.fill()
        GLib.idle_add(lambda: self.search.grab_focus() and False)

    def fill(self):
        tui.clear(self.list)
        q = self.search.get_text().casefold()
        first = True
        for app, rec in self.apps:
            name = app.get_display_name()
            if q and q not in name.casefold() and q not in (app.get_executable() or "").casefold():
                continue
            row = Gtk.ListBoxRow()
            row.app = app
            row.set_child(tui.hbox(tui.label("★" if rec else " ", "accent"), tui.label(name, hexpand=True),
                                   tui.label(os.path.basename(app.get_executable() or ""), "dim"), spacing=8))
            self.list.append(row)
            if first:
                self.list.select_row(row)
                first = False

    def do(self):
        cmd = self.cmd.get_text().strip()
        if cmd:
            try:
                app = Gio.AppInfo.create_from_commandline(cmd if "%" in cmd else cmd + " %F", None,
                                                          Gio.AppInfoCreateFlags.SUPPORTS_URIS)
            except GLib.Error as e:
                self.cmd.set_text("")
                self.cmd.set_placeholder_text(e.message)
                return
        else:
            row = self.list.get_selected_row()
            if row is None:
                return
            app = row.app
        if self.make_default.active:
            self.set_default_cb(self.items[0].ctype, app.get_id())
        self.close()
        self.launch(app, self.items)


# ---------------------------------------------------------------- Packen
def compress(parent, items, folder_path, cb):
    d = Dialog(parent, "packen")
    base = items[0].display if len(items) == 1 else os.path.basename(folder_path) or "archiv"
    if len(items) == 1 and not items[0].is_dir and "." in base[1:]:
        base = base[:base.rfind(".")]
    name = tui.Entry(text=base)
    fmt = {"v": "zip"}
    sel = tui.Select(lambda v: fmt.__setitem__("v", v))
    sel.set_options([("zip", ".zip"), ("tar.gz", ".tar.gz"), ("tar.xz", ".tar.xz"), ("7z", ".7z"), ("tar", ".tar")],
                    "zip")
    d.body.append(tui.hbox(name, sel, spacing=8))
    err = tui.label("", "warn")
    d.body.append(err)

    def ok():
        n = name.get_text().strip()
        out = os.path.join(folder_path, f"{n}.{fmt['v']}")
        msg = name_error(f"{n}.{fmt['v']}", folder_path)
        if msg:
            err.set_label(msg)
            return
        d.close()
        cb(out, fmt["v"])
    name.connect("activate", lambda *_: ok())
    d.button("abbrechen", d.close)
    d.button("packen", ok, default=True)
    d.present()
    GLib.idle_add(lambda: (name.grab_focus(), name.select_region(0, -1)) and False)


# ---------------------------------------------------------------- Server verbinden
def connect_server(parent, cb):
    d = Dialog(parent, "mit server verbinden")
    d.body.append(tui.label("Adresse, z. B. sftp://user@rechner/pfad, smb://nas/freigabe oder ftp://…", "dim",
                            wrap=True))
    e = tui.Entry(text="sftp://")
    d.body.append(e)

    def ok():
        uri = e.get_text().strip()
        if "://" in uri:
            d.close()
            cb(uri)
    e.connect("activate", lambda *_: ok())
    d.button("abbrechen", d.close)
    d.button("verbinden", ok, default=True)
    d.present()
    GLib.idle_add(lambda: (e.grab_focus(), e.set_position(-1)) and False)
