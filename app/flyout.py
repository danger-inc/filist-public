# Rechtsklick-Menü aus einem Gio.Menu-Modell, bei dem Untermenüs beim Überfahren (Hover) neben dem Eintrag
# aufklappen – statt in GTKs eigenem Umschalt-Menü, das den Inhalt austauscht und sich wie ein neues Fenster anfühlt.
from gi.repository import Gdk, Gio, GLib, Gtk

OPEN_MS = 110          # Wartezeit, bevor ein Untermenü beim Überfahren aufgeht
CLOSE_MS = 260         # Wartezeit, bevor es zugeht, wenn der Zeiger woanders hin wandert


def close_on_outside(pop, owner):
    """Menü ohne GTK-Grab: Klick irgendwo anders, Scrollen, Esc oder Fensterwechsel schließen es – der Klick bzw. das
    Scrollen kommt trotzdem an."""
    root = owner.get_root()
    if root is None:
        return
    t0 = GLib.get_monotonic_time()

    def close(*_):
        if GLib.get_monotonic_time() - t0 > 150_000:        # das Ereignis, das das Menü öffnete, ignorieren
            pop.popdown()
        return False
    click = Gtk.GestureClick(button=0)
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", close)
    scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
    scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    scroll.connect("scroll", close)
    key = Gtk.EventControllerKey()
    key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    key.connect("key-pressed", lambda c, kv, *_: (pop.popdown(), True)[1] if kv == Gdk.KEY_Escape else False)
    for c in (click, scroll, key):
        root.add_controller(c)
    hid = root.connect("notify::is-active", lambda w, p: None if w.is_active() else pop.popdown())

    def done(*_):
        root.disconnect(hid)

        def drop():                                          # nie mitten im Ereignis entfernen (Absturz)
            for c in (click, scroll, key):
                root.remove_controller(c)
            return False
        GLib.idle_add(drop)
    pop.connect("closed", done)


_active = None


class Fly:
    def __init__(self, model, target, custom=None):
        """model: Gio.Menu · target: Widget, auf dem Aktionen („win.…“) aufgelöst werden · custom: {name: Widget}"""
        self.target, self.custom = target, custom or {}
        self.pops = []                       # alle offenen Popovers (Wurzel zuerst)
        self.root = self._build(model, None, parent=None)

    # ---------------------------------------------------------------- Aufbau
    def _build(self, model, row, parent):
        pop = Gtk.Popover(has_arrow=False, autohide=False, position=Gtk.PositionType.RIGHT)
        pop.add_css_class("fw-menu")
        pop.add_css_class("fw-fly")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        pop.set_child(box)
        pop._rows, pop._open, pop._timer = [], None, 0
        first = True
        for i in range(model.get_n_items()):
            sec = model.get_item_link(i, Gio.MENU_LINK_SECTION)
            if sec is None:                  # Eintrag direkt (ohne Abschnitt)
                self._item(pop, box, model, i)
                continue
            if not first:
                box.append(Gtk.Separator())
            first = False
            for j in range(sec.get_n_items()):
                self._item(pop, box, sec, j)
        return pop

    def _item(self, pop, box, model, i):
        custom = model.get_item_attribute_value(i, "custom", None)
        if custom is not None:
            w = self.custom.get(custom.get_string())
            if w is not None:
                box.append(w)
            return
        label = model.get_item_attribute_value(i, "label", None)
        label = label.get_string() if label else ""
        action = model.get_item_attribute_value(i, "action", None)
        sub = model.get_item_link(i, Gio.MENU_LINK_SUBMENU)
        row = Gtk.Button(focus_on_click=False)
        row.add_css_class("fw-mi")
        inner = Gtk.Box(spacing=12)
        lab = Gtk.Label(label=label, xalign=0, hexpand=True)
        inner.append(lab)
        if sub is not None:
            inner.append(Gtk.Label(label="›", css_classes=["fw-arrow"]))
        row.set_child(inner)
        box.append(row)
        pop._rows.append(row)
        if sub is not None:
            row._sub = sub
            row.connect("clicked", lambda b: self._open(pop, b))
        elif action is not None:
            detail = action.get_string()
            row.connect("clicked", lambda b, d=detail: self._run(d))
        mc = Gtk.EventControllerMotion()
        mc.connect("enter", lambda c, x, y, b=row: self._enter(pop, b))
        row.add_controller(mc)

    # ---------------------------------------------------------------- Hover
    def _enter(self, pop, row):
        if pop._timer:
            GLib.source_remove(pop._timer)
            pop._timer = 0
        if getattr(row, "_sub", None) is not None:
            if pop._open is not None and pop._open[0] is row:
                return
            pop._timer = GLib.timeout_add(OPEN_MS, lambda: (self._open(pop, row), False)[1])
        elif pop._open is not None:
            pop._timer = GLib.timeout_add(CLOSE_MS, lambda: (self._close_sub(pop), False)[1])

    def _open(self, pop, row):
        pop._timer = 0
        if pop._open is not None:
            if pop._open[0] is row:
                return
            self._close_sub(pop)
        child = self._build(row._sub, row, parent=pop)
        child.set_parent(row)
        # Zeiger im Untermenü: nichts schließen
        mc = Gtk.EventControllerMotion()
        mc.connect("enter", lambda *_: pop._timer and (GLib.source_remove(pop._timer), setattr(pop, "_timer", 0)))
        child.add_controller(mc)
        pop._open = (row, child)
        self.pops.append(child)
        child.popup()

    def _close_sub(self, pop):
        pop._timer = 0
        if pop._open is None:
            return
        row, child = pop._open
        pop._open = None
        for r in child._rows:
            if child._open is not None:
                self._close_sub(child)
        if child in self.pops:
            self.pops.remove(child)
        child.popdown()
        GLib.idle_add(lambda: (child.unparent(), False)[1])

    # ---------------------------------------------------------------- Ausführen
    def _run(self, detail):
        name, tgt = Gio.Action.parse_detailed_name(detail)[-2:]
        self.close()
        GLib.idle_add(lambda: (self.target.activate_action(name, tgt), False)[1])

    def close(self):
        self._close_sub(self.root)
        self.root.popdown()

    def popup(self, parent, x, y):
        global _active
        if _active is not None:
            _active.close()
        _active = self
        self.root.set_position(Gtk.PositionType.BOTTOM)
        self.root.set_halign(Gtk.Align.START)
        self.root.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.root.set_pointing_to(rect)

        def closed(p):
            if p._timer:
                GLib.source_remove(p._timer)
            self._close_sub(p)
            GLib.idle_add(lambda: (p.unparent(), False)[1])
        self.root.connect("closed", closed)
        self.root.popup()
        close_on_outside(self.root, parent)
