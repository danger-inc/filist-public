# Vorschau (rechts, Leertaste) und Aufgaben-Leiste (unten, während kopiert/gepackt/… wird).
import os, threading, time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango

import icons
import model
import thumbs
import tui

TEXT_MAX = 64 * 1024
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-shellscript", "application/javascript",
              "application/x-yaml", "application/toml", "application/x-desktop", "application/sql")


PREV_MIN = 100   # schmalste Vorschau (Griff lässt sich bis hierhin nach rechts ziehen)


class Preview(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=False)
        self.add_css_class("fw-preview")
        self.set_size_request(PREV_MIN, -1)
        self.gen = 0
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, vexpand=False)
        self.pic.set_size_request(-1, 220)
        self.pic.add_css_class("fw-prev-pic")
        self.glyph = tui.label("", "fw-prev-glyph", xalign=0.5)
        self.text = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR,
                                 top_margin=4, left_margin=4, right_margin=4)
        self.text.add_css_class("fw-prev-text")
        self.text_sc = Gtk.ScrolledWindow(child=self.text, vexpand=True, min_content_height=200)
        self.name = tui.label("", "fw-prev-name", wrap=True)
        self.name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.info = Gtk.Grid(column_spacing=10, row_spacing=3)
        self.listing = tui.vbox(spacing=1)
        for w in (self.pic, self.glyph, self.text_sc, self.name, self.info, self.listing):
            self.append(w)
        self.extra = None              # Zusatz unter der Vorschau mit .show(item|None)
        self.show_items([])

    def _clear(self):
        self.pic.set_paintable(None)
        self.pic.set_visible(False)
        self.glyph.set_visible(False)
        self.text_sc.set_visible(False)
        tui.clear(self.info)
        tui.clear(self.listing)

    def _row(self, r, k, v):
        self.info.attach(tui.label(k, "dim"), 0, r, 1, 1)
        l = tui.label(v, ellipsize=True, hexpand=True)
        l.set_tooltip_text(v)
        self.info.attach(l, 1, r, 1, 1)

    def show_items(self, items, folder=None):
        self.gen += 1
        gen = self.gen
        self._clear()
        if self.extra is not None:
            self.extra.show(items[0] if len(items) == 1 else None)
        if not items:
            self.glyph.set_label(icons.FOLDER_OPEN)
            self.glyph.set_visible(True)
            self.name.set_label(folder.title() if folder else "")
            if folder is not None:
                self._row(0, "elemente", str(len(folder.shown)))
                p = folder.local_path()
                if p:
                    free, total = model.disk_free(p)
                    if total:
                        self._row(1, "frei", f"{model.human_size(free)} von {model.human_size(total)}")
            return
        if len(items) > 1:
            self.glyph.set_label("\U000F0222")
            self.glyph.set_visible(True)
            files = [i for i in items if not i.is_dir]
            self.name.set_label(f"{len(items)} ausgewählt")
            self._row(0, "dateien", str(len(files)))
            self._row(1, "ordner", str(len(items) - len(files)))
            self._row(2, "größe", model.human_size(sum(i.size or 0 for i in files)) +
                      (" + ordner" if len(files) < len(items) else ""))
            return
        it = items[0]
        self.name.set_label(it.display)
        rows = [("typ", it.type_text), ("größe", model.human_size(it.size) if not it.is_dir else
                                        (f"{it.dir_count} elemente" if it.dir_count is not None else "–")),
                ("geändert", it.date_text())]
        if it.path:
            try:
                st = os.lstat(it.path)
                rows.append(("rechte", f"{stat_text(st.st_mode)}"))
            except OSError:
                pass
            rows.append(("ort", icons.home_relative(os.path.dirname(it.path))))
        if it.extra.get("orig_path"):
            rows.append(("ursprung", icons.home_relative(it.extra["orig_path"])))
        for r, (k, v) in enumerate(rows):
            self._row(r, k, v)
        self.glyph.set_label(it.glyph)
        self.glyph.set_css_classes(["fw-prev-glyph", "g-" + it.group])
        self.glyph.set_visible(True)
        if it.is_dir and it.path:
            self._dir_listing(it, gen)
        elif it.group == "img" and it.path:
            self._image(it, gen)
        elif it.path and (it.ctype.startswith(TEXT_TYPES) or it.group in ("code", "txt")) and it.size < 20 * 1024 * 1024:
            self._text(it, gen)
        elif thumbs.get().can(it):
            thumbs.get().get(it, lambda tex: gen == self.gen and self._show_pic(tex), "large")

    def _show_pic(self, tex):
        self.pic.set_paintable(tex)
        self.pic.set_visible(True)
        self.glyph.set_visible(False)

    def _image(self, it, gen):
        def work():
            try:
                tex = Gdk.Texture.new_from_filename(it.path) if it.size < 60 * 1024 * 1024 else None
            except GLib.Error:
                tex = None
            if tex is not None:
                GLib.idle_add(lambda: gen == self.gen and self._show_pic(tex) and False)
            else:
                GLib.idle_add(lambda: thumbs.get().get(it, lambda t: gen == self.gen and self._show_pic(t), "large")
                              and False)
        threading.Thread(target=work, daemon=True).start()

    def _text(self, it, gen):
        def work():
            try:
                with open(it.path, "rb") as f:
                    data = f.read(TEXT_MAX)
                if b"\0" in data[:4096]:
                    return
                txt = data.decode("utf-8", errors="replace")
                if len(data) == TEXT_MAX:
                    txt += "\n…"
            except OSError:
                return

            def show():
                if gen != self.gen:
                    return False
                self.text.get_buffer().set_text(txt)
                self.text_sc.set_visible(True)
                self.glyph.set_visible(False)
                return False
            GLib.idle_add(show)
        threading.Thread(target=work, daemon=True).start()

    def _dir_listing(self, it, gen):
        def work():
            try:
                with os.scandir(it.path) as sc:
                    names = sorted((e.name + ("/" if e.is_dir() else "") for e in sc if not e.name.startswith(".")),
                                   key=str.casefold)
            except OSError:
                return

            def show():
                if gen != self.gen:
                    return False
                for n in names[:18]:
                    self.listing.append(tui.label(n, "dim", ellipsize=True))
                if len(names) > 18:
                    self.listing.append(tui.label(f"… und {len(names) - 18} weitere", "dim"))
                if not names:
                    self.listing.append(tui.label("leer", "dim"))
                return False
            GLib.idle_add(show)
        threading.Thread(target=work, daemon=True).start()


def stat_text(mode):
    out = ""
    for who in (6, 3, 0):
        for bit, ch in ((4, "r"), (2, "w"), (1, "x")):
            out += ch if mode & (bit << who) else "-"
    return f"{out}  ({mode & 0o777:03o})"


class Jobs(Gtk.Box):
    """Laufende Aufgaben als Zeilen: Titel, Balken, Tempo/Restzeit, Abbrechen."""

    def __init__(self, ops):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("fw-jobs")
        self.ops = ops
        self.rows = {}
        ops.listeners.append(self.update)
        self.set_visible(False)

    def update(self, job):
        row = self.rows.get(job)
        if job not in self.ops.jobs:
            if row:
                self.remove(row)
                del self.rows[job]
                getattr(tui, "release_later", lambda *w: None)(row)
            self.set_visible(bool(self.rows))
            return
        if row is None:
            row = tui.vbox(spacing=2)
            row.title = tui.label("", ellipsize=True, hexpand=True)
            row.info = tui.label("", "dim")
            row.cancel = tui.Button("✕", job.stop, tooltip="Abbrechen")
            row.cancel.add_css_class("close")
            row.meter = tui.Meter(1000, interactive=False, small=True, mark=False, flush=True)
            row.append(tui.hbox(row.title, row.info, row.cancel, spacing=8))
            row.append(row.meter)
            self.append(row)
            self.rows[job] = row
        frac = job.fraction
        done = job.state != "läuft"
        row.title.set_label(job.title + (f" · {job.current}" if job.current and not done else ""))
        if done:
            row.info.set_label({"fertig": "✓ fertig", "abgebrochen": "abgebrochen",
                                "fehler": "✗ " + (job.error or "fehler")}.get(job.state, job.state))
            row.cancel.set_visible(False)
            row.meter.set_value(1000 if job.state == "fertig" else int((frac or 0) * 1000))
        else:
            parts = []
            if job.total_files:
                parts.append(f"{job.done_files}/{job.total_files}")
            sp = job.speed()
            if sp:
                parts.append(model.human_size(int(sp)) + "/s")
                if job.total_bytes and sp:
                    rest = (job.total_bytes - job.done_bytes) / sp
                    parts.append(f"noch {fmt_eta(rest)}")
            row.info.set_label(" · ".join(parts))
            row.meter.set_value(int((frac or 0) * 1000))
        self.set_visible(True)


def fmt_eta(s):
    s = int(max(0, s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"
