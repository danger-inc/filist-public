# Aufräum-Fenster von filist: vier Reiter – aufräumen (Ordner), speicher freigeben (System), große dateien,
# sortieren. Die Logik steht in cleanup.py. Gelöscht wird nie ohne Vorschau und Bestätigung; Nutzerdateien
# gehen in den Papierkorb (rückgängig mit Strg+Z im Hauptfenster), nur Caches/Papierkorb werden wirklich gelöscht.
import os, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

import cleanup
import dialogs
import model
import tui
from cleanup import human

CSS = """
window.cl-win { background-color: @background; }
window.cl-win.glass { background-color: alpha(@background, 0.72); }
window.cl-win label { font-size: 13px; }
.cl-body { padding: 4px 14px 14px 14px; }
window.cl-win .cl-sub { color: alpha(@foreground, 0.6); font-size: 12px; }
window.cl-win .cl-note { color: alpha(@foreground, 0.5); font-size: 11px; }
window.cl-win .cl-size { color: @secondary; font-size: 12px; }
window.cl-win .cl-bar { font-family: monospace; color: @primary; font-size: 12px; }
window.cl-win .cl-big { font-size: 26px; font-weight: bold; color: @primary; }
window.cl-win .cl-warn { color: @tertiary; }
.cl-tabs { padding: 2px 14px 6px 14px; }
button.cl-tab { background: none; border: 1px solid alpha(@primary, 0.3); border-radius: 12px; padding: 1px 10px;
    min-height: 0; box-shadow: none; }
button.cl-tab:hover { background-color: alpha(@primary, 0.15); }
button.cl-tab.on { background-color: alpha(@primary, 0.28); border-color: @primary; }
window.cl-win button.cl-tab label { font-size: 12px; color: @foreground; }
.cl-actions { padding: 6px 14px; border-top: 1px solid alpha(@primary, 0.25); }
button.cl-show { background: none; border: none; box-shadow: none; padding: 0 4px; min-height: 0; min-width: 0; }
window.cl-win button.cl-show label { color: alpha(@foreground, 0.55); }
button.cl-show:hover { background-color: alpha(@primary, 0.15); }
"""

TABS = [("tidy", "aufräumen"), ("space", "speicher freigeben"), ("big", "große dateien"), ("sort", "sortieren")]
FLAT = ("temp", "broken", "empty_f", "empty_d")        # eine Zeile je Datei statt je Fund
SHOW = "\U000F0208"
MAX_ROWS = 300


def show_in_filist(win, path):
    win.pane.go(model.uri_for(os.path.dirname(path)), select=model.uri_for(path))
    win.present()


def age(mtime):
    d = (time.time() - mtime) / cleanup.DAY
    if d < 1:
        return "heute"
    if d < 60:
        return f"vor {int(d)} Tagen"
    if d < 700:
        return f"vor {int(d / 30)} Monaten"
    return f"vor {int(d / 365)} Jahren"


class Entry:
    def __init__(self, check, paths, size, mode):
        self.check, self.paths, self.size, self.mode = check, paths, size, mode


class CleanupWindow(Gtk.Window):
    def __init__(self, win, folder, tab="tidy"):
        super().__init__(title="filist · aufräumen", default_width=760, default_height=760,
                         application=win.get_application(), transient_for=win)
        self.win, self.folder = win, folder
        for c in ("pb-window", "tui", "cl-win"):
            self.add_css_class(c)
        self.set_decorated(False)
        try:
            import glass
            getattr(glass, "frame_window", lambda w: None)(self)
        except ImportError:
            pass
        head = tui.hbox(spacing=8)
        head.add_css_class("fw-dhead")
        name = os.path.basename((folder or "").rstrip("/")) or folder or ""
        self.title_lbl = tui.label(f"aufräumen · {name}" if folder else "aufräumen", "fw-dtitle", hexpand=True,
                                   ellipsize=True)
        head.append(self.title_lbl)
        close = tui.Button("✕", self.close, tooltip="Schließen (Esc)")
        close.add_css_class("close")
        head.append(close)
        self.tab_btns = {}
        tabs = tui.hbox(spacing=6)
        tabs.add_css_class("cl-tabs")
        for tid, text in TABS:
            b = Gtk.Button(focus_on_click=False, label=text)
            b.add_css_class("cl-tab")
            b.connect("clicked", lambda _b, t=tid: self.switch(t))
            tabs.append(b)
            self.tab_btns[tid] = b
        self.body = tui.vbox(spacing=10)
        self.body.add_css_class("cl-body")
        sc = Gtk.ScrolledWindow(child=self.body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.sum_lbl = tui.label("", "cl-sub", hexpand=True, ellipsize=True)
        self.act_btn = tui.Button("", self.apply)
        self.act_btn.add_css_class("on")
        self.undo_btn = tui.Button("rückgängig", self.undo_sort)
        self.undo_btn.set_visible(False)
        self.actions = tui.hbox(self.sum_lbl, self.undo_btn, self.act_btn, spacing=8)
        self.actions.add_css_class("cl-actions")
        self.status = tui.label("", "cl-note", ellipsize=True)
        self.status.set_margin_start(14)
        self.status.set_margin_bottom(8)
        self.set_child(tui.vbox(Gtk.WindowHandle(child=head), tabs, sc, self.actions, self.status, spacing=0))
        k = Gtk.EventControllerKey()
        k.connect("key-pressed", lambda c, kv, *_: (self.close(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(k)
        self.connect("close-request", self._closing)
        self.cancelled = False
        self.token = 0
        self.entries = []
        self.tab = None
        self.sort_mode = "typ"
        self.moved = None
        self.switch(tab)
        self.present()

    def _closing(self, *_):
        self.cancelled = True
        self.token += 1
        GLib.timeout_add(1500, lambda: (tui.release_tree(self), False)[1])
        return False

    # ------------------------------------------------------------------ Gerüst
    def say(self, text, error=False):
        self.status.set_label(("✗ " if error else "") + text)
        if error:
            self.win.say(text, error=True)

    def switch(self, tab):
        if tab != "space" and not self.folder:
            tab = "space"
        self.tab = tab
        for t, b in self.tab_btns.items():
            (b.add_css_class if t == tab else b.remove_css_class)("on")
            b.set_sensitive(t == "space" or bool(self.folder))
        self.token += 1
        self.entries = []
        self.undo_btn.set_visible(False)
        self.sum_lbl.set_label("")
        self.act_btn.set_visible(False)
        self.say("")
        getattr(self, "run_" + tab)()

    def busy(self, text):
        tui.clear(self.body)
        sp = Gtk.Spinner(spinning=True, width_request=30, height_request=30, halign=Gtk.Align.CENTER)
        self.busy_lbl = tui.label(text, "cl-sub", xalign=0.5)
        self.body.append(tui.vbox(sp, self.busy_lbl, spacing=10, valign=Gtk.Align.CENTER, vexpand=True, margin_top=120))

    def progress(self, i, n):
        tok = self.token

        def upd():
            if tok == self.token and hasattr(self, "busy_lbl") and self.busy_lbl.get_parent():
                self.busy_lbl.set_label(f"{i} / {n} …" if n else f"{i} dateien …")
            return False
        GLib.idle_add(upd)

    def scan(self, text, fn, show):
        """fn(progress, cancel) im Thread; show(ergebnis) nur, wenn der Reiter noch aktuell ist."""
        self.busy(text)
        tok = self.token

        def done(res):
            if tok != self.token or self.cancelled:
                return
            tui.clear(self.body)
            if isinstance(res, Exception):
                self.body.append(tui.label(f"✗ {res}", "cl-warn"))
                return
            show(res)
        tui.background(lambda: fn(self.progress, lambda: tok != self.token), done=done)

    def panel(self, title, child=None):
        p = tui.Panel(title, child)
        self.body.append(p)
        return p

    def chip(self, text, cb, tip=None):
        b = Gtk.Button(focus_on_click=False, tooltip_text=tip)
        b.add_css_class("cl-tab")
        b.set_child(tui.label(text))
        b.connect("clicked", lambda _b: cb())
        return b

    def head_line(self, big, text, sub):
        self.body.append(tui.hbox(tui.label(big, "cl-big"),
                                  tui.vbox(tui.label(text, "fw-dtitle"), tui.label(sub, "cl-sub"), spacing=0,
                                           valign=Gtk.Align.CENTER), spacing=14))

    def add_row(self, box, title, size_text, note, paths, size, mode, checked, selectable=True, show=None):
        """Zeile mit Haken (oder ohne), Name, Größe und Pfeil „im Ordner zeigen“."""
        row = tui.hbox(spacing=8)
        ent = None
        if selectable:
            chk = tui.Check("", checked, lambda _on: self.update_sum())
            row.append(chk)
            ent = Entry(chk, paths, size, mode)
            self.entries.append(ent)
        text = tui.vbox(tui.label(title, ellipsize=True), tui.label(note, "cl-note", ellipsize=True) if note else None,
                        spacing=0, hexpand=True)
        text.set_valign(Gtk.Align.CENTER)
        row.append(text)
        row.append(tui.label(size_text, "cl-size"))
        if show:
            b = Gtk.Button(label=SHOW, focus_on_click=False, tooltip_text="im Ordner zeigen")
            b.add_css_class("cl-show")
            b.connect("clicked", lambda _b: show_in_filist(self.win, show))
            row.append(b)
        box.append(row)
        return ent

    def select_chips(self, ents):
        def setall(on):
            for e in ents:
                e.check.set_active(on)
            self.update_sum()
        return tui.hbox(self.chip("alle", lambda: setall(True)), self.chip("keine", lambda: setall(False)), spacing=6)

    def update_sum(self):
        sel = [e for e in self.entries if e.check.active]
        n = sum(len(e.paths) for e in sel)
        self.sum_lbl.set_label(f"{n} gewählt · {human(sum(e.size for e in sel))}" if sel else "nichts gewählt")
        self.act_btn.set_sensitive(bool(sel))

    def show_actions(self, text):
        self.act_btn.set_label(text)
        self.act_btn.set_visible(True)
        self.update_sum()

    def rescan(self):
        self.switch(self.tab)

    # ------------------------------------------------------------------ Reiter: aufräumen
    def run_tidy(self):
        self.scan("suche nach aufräumbarem …", lambda p, c: cleanup.tidy_scan(self.folder, p, c), self.show_tidy)

    def show_tidy(self, items):
        waste = sum(i.size for i in items)
        if not items:
            self.head_line("✓", "hier ist alles ordentlich", "keine temporären, leeren oder doppelten dateien")
            return
        self.head_line(human(waste), "lassen sich hier aufräumen",
                       "vorgeschlagen, nicht entschieden – du wählst aus, es geht in den papierkorb")
        by = {}
        for it in items:
            by.setdefault(it.group, []).append(it)
        order = ("temp", "broken", "empty_d", "empty_f", "dups")
        for g in order:
            its = by.get(g)
            if not its:
                continue
            box = tui.vbox(spacing=3)
            first = len(self.entries)
            shown = 0
            if g in FLAT:
                it = its[0]
                for p in it.paths[:MAX_ROWS]:
                    try:
                        sz = cleanup.disk_use(os.lstat(p)) if g in ("temp",) else 0
                    except OSError:
                        sz = 0
                    self.add_row(box, os.path.relpath(p, self.folder), human(sz) if sz else "", "", [p], sz, "trash",
                                 it.checked, show=p)
                    shown += 1
                total, title = len(it.paths), it.title
                note = it.note
            else:
                for it in its[:MAX_ROWS]:
                    self.add_row(box, it.title, human(it.size), f"{len(it.paths)} × entfernen · {it.note}", it.paths,
                                 it.size, "trash", it.checked, show=it.paths[0])
                    shown += 1
                total, title = len(its), "Doppelte Dateien"
                note = "behalten wird jeweils die älteste – die anderen Kopien kannst du wählen"
            if total > shown:
                box.append(tui.label(f"… {total - shown} weitere (nicht angezeigt)", "cl-note"))
            ents = self.entries[first:]
            sz = sum(i.size for i in its)
            p = self.panel(f"{title} · {total}" + (f" · {human(sz)}" if sz else ""))
            p.body.append(tui.label(note, "cl-note", wrap=True))
            p.body.append(self.select_chips(ents))
            p.body.append(box)
        self.show_actions("in den papierkorb")

    # ------------------------------------------------------------------ Reiter: speicher
    def run_space(self):
        self.scan("messe speicherplatz … (cache und papierkorb können dauern)",
                  lambda p, c: cleanup.system_items(p, c), self.show_space)

    def show_space(self, items):
        total = sum(i.size for i in items if i.mode != "cmd")
        try:
            st = os.statvfs(os.path.expanduser("~"))
            free = human(st.f_bavail * st.f_frsize)
        except OSError:
            free = "?"
        self.head_line(human(total), "könnten frei werden", f"aktuell frei auf dem laufwerk: {free}")
        if not items:
            self.body.append(tui.label("nichts nennenswertes gefunden", "cl-sub"))
            return
        box = tui.vbox(spacing=3)
        cmds = tui.vbox(spacing=3)
        n_sel = 0
        for it in items:
            if it.mode == "cmd":
                row = tui.hbox(tui.vbox(tui.label(it.title), tui.label(it.note, "cl-note", wrap=True), spacing=0,
                                        hexpand=True), tui.label(human(it.size), "cl-size"), spacing=8)
                row.append(self.chip("befehl kopieren", lambda c=it.cmd: (self.get_clipboard().set(c),
                                                                          self.say(f"kopiert: {c}"))))
                cmds.append(row)
            else:
                self.add_row(box, it.title, human(it.size), it.note + ("" if it.mode == "trash"
                                                                       else " · wird endgültig gelöscht"),
                             it.paths, it.size, it.mode, it.checked,
                             show=it.paths[0] if it.paths else None)
                n_sel += 1
        p = self.panel("bereinigen")
        p.body.append(self.select_chips(self.entries))
        p.body.append(box)
        if cmds.get_first_child():
            self.panel("mit administratorrechten", cmds)
        self.show_actions("bereinigen")

    # ------------------------------------------------------------------ Reiter: große Dateien
    def run_big(self):
        self.scan("suche große dateien …", lambda p, c: cleanup.big_scan(self.folder, p, c), self.show_big)

    def show_big(self, res):
        files, folders, total = res
        self.head_line(human(total), "belegt dieser ordner", f"{len(files)} dateien über 5 MB aufgelistet · "
                                                              "die größten zuerst")
        if folders:
            box = tui.vbox(spacing=2)
            top = folders[0][0] or 1
            for size, path in folders[:12]:
                bar = "█" * max(1, round(24 * size / top))
                row = tui.hbox(tui.label(os.path.basename(path), ellipsize=True, hexpand=True),
                               tui.label(bar, "cl-bar"), tui.label(human(size), "cl-size"), spacing=8)
                b = Gtk.Button(focus_on_click=False, tooltip_text="im ordner zeigen")
                b.add_css_class("cl-show")
                b.set_child(row)
                b.connect("clicked", lambda _b, p=path: (self.win.pane.go(model.uri_for(p)), self.win.present()))
                box.append(b)
            self.panel("größte unterordner", box)
        if files:
            box = tui.vbox(spacing=3)
            first = len(self.entries)
            for size, path, mt in files:
                self.add_row(box, os.path.relpath(path, self.folder), human(size), age(mt) + " geändert", [path], size,
                             "trash", False, show=path)
            p = self.panel("größte dateien")
            p.body.append(self.select_chips(self.entries[first:]))
            p.body.append(box)
            self.show_actions("in den papierkorb")

    # ------------------------------------------------------------------ Reiter: sortieren
    def run_sort(self):
        self.busy("plane …")
        tok = self.token
        tui.background(lambda: cleanup.sort_plan(self.folder, self.sort_mode),
                       done=lambda plan: tok == self.token and self.show_sort(plan))

    def show_sort(self, plan):
        tui.clear(self.body)
        if isinstance(plan, Exception):
            self.body.append(tui.label(f"✗ {plan}", "cl-warn"))
            return
        self.plan = plan
        modes = tui.hbox(spacing=6)
        for m, text in cleanup.MODES.items():
            b = self.chip(text, lambda m=m: self.set_mode(m))
            if m == self.sort_mode:
                b.add_css_class("on")
            modes.append(b)
        self.body.append(modes)
        if not plan:
            self.body.append(tui.label("keine losen dateien in diesem ordner – unterordner und versteckte dateien "
                                       "bleiben unberührt", "cl-sub", wrap=True))
            return
        groups = {}
        for src, dst in plan:
            n, s = groups.get(dst, (0, 0))
            try:
                sz = os.lstat(src).st_size
            except OSError:
                sz = 0
            groups[dst] = (n + 1, s + sz)
        self.head_line(str(len(plan)), "lose dateien werden einsortiert",
                       "unterordner und versteckte dateien bleiben unberührt · gleiche namen werden nummeriert")
        box = tui.vbox(spacing=3)
        for dst, (n, s) in sorted(groups.items()):
            new = "" if os.path.isdir(dst) else " · neuer ordner"
            box.append(tui.hbox(tui.label(os.path.relpath(dst, self.folder) + "/", ellipsize=True, hexpand=True),
                                tui.label(f"{n} dateien{new}", "cl-note"), tui.label(human(s), "cl-size"), spacing=8))
        self.panel("vorschau", box)
        self.act_btn.set_label("sortieren")
        self.act_btn.set_visible(True)
        self.act_btn.set_sensitive(True)
        self.sum_lbl.set_label(f"{len(plan)} dateien werden verschoben – rückgängig möglich")

    def set_mode(self, m):
        self.sort_mode = m
        self.switch("sort")

    # ------------------------------------------------------------------ Ausführen
    def apply(self):
        if self.tab == "sort":
            return self.do_sort()
        sel = [e for e in self.entries if e.check.active]
        if not sel:
            return
        trash = [p for e in sel if e.mode == "trash" for p in e.paths]
        dele = [p for e in sel if e.mode == "delete" for p in e.paths]
        size = sum(e.size for e in sel)
        lines = []
        if trash:
            lines.append(f"{len(trash)} {'Eintrag' if len(trash) == 1 else 'Einträge'} kommen in den Papierkorb.")
        if dele:
            lines.append(f"{len(dele)} {'Eintrag wird' if len(dele) == 1 else 'Einträge werden'} endgültig gelöscht "
                         "(Caches/Papierkorb) – das lässt sich nicht rückgängig machen.")
        text = "\n".join(lines) + f"\n\nZusammen {human(size)}."
        dialogs.confirm(self, "aufräumen", text, "ausführen", lambda: self.execute(trash, dele, size),
                        danger=bool(dele))

    def execute(self, trash, dele, size):
        ops = self.win.ops
        uri = lambda p: Gio.File.new_for_path(p).get_uri()      # noqa: E731
        steps = []
        if trash:
            steps.append(lambda cb: ops.trash([uri(p) for p in trash], done=cb))
        if dele:
            steps.append(lambda cb: ops.delete([uri(p) for p in dele], done=cb))
        self.act_btn.set_sensitive(False)
        self.say("räume auf …")
        failed = []

        def run(i=0, job=None):
            if job is not None and job.state != "fertig":
                failed.append(job)
            if i < len(steps):
                steps[i](lambda j, i=i: run(i + 1, j))
                return
            if self.cancelled:
                return
            if failed:
                self.say("nicht alles ging: " + (failed[0].error or "siehe Aufgaben"), error=True)
            else:
                self.say(f"fertig · bis zu {human(size)} frei" + (" · Strg+Z im Hauptfenster macht den Papierkorb "
                                                                   "rückgängig" if trash else ""))
            self.win.say(f"aufgeräumt: {human(size)}")
            tab = self.tab
            self.token += 1
            GLib.timeout_add(300, lambda: (not self.cancelled and self.tab == tab and self._rescan_keep(), False)[1])
        run()

    def _rescan_keep(self):
        msg = self.status.get_label()
        self.switch(self.tab)
        self.say(msg.removeprefix("✗ "), error=msg.startswith("✗"))

    def do_sort(self):
        plan = self.plan
        self.act_btn.set_sensitive(False)
        self.say("sortiere …")
        tok = self.token

        def done(res):
            if isinstance(res, Exception):
                self.say(str(res), error=True)
                return
            moved, errs, made = res
            self.moved = (moved, made, self.folder)
            if tok != self.token or self.cancelled:
                return
            self.switch("sort")
            self.say(f"{len(moved)} dateien sortiert" + (f", {len(errs)} nicht möglich: {errs[0]}" if errs else ""),
                     error=bool(errs and not moved))
            self.undo_btn.set_visible(bool(moved))
        tui.background(lambda: cleanup.apply_moves(plan), done=done)

    def undo_sort(self):
        if not self.moved:
            return
        moved, made, _folder = self.moved
        self.moved = None

        def done(errs):
            self.switch("sort")
            self.say("zurückgelegt" + (f" · {errs} nicht möglich" if errs else ""), error=bool(errs))
        tui.background(lambda: cleanup.undo_moves(moved, made), done=done)
