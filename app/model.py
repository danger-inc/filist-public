# Datenmodell: ein Eintrag pro Datei (FileItem) und das Einlesen von Ordnern.
# Lokale Ordner: os.scandir in einem Hintergrund-Thread (sehr schnell, auch bei zehntausenden Dateien).
# Alles andere (trash:///, recent:///, smb://, sftp:// …) über Gio, ebenfalls asynchron.
import datetime, locale, os, re, shlex, stat, threading, time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject

import icons

_NUM = re.compile(r"(\d+)")


def natural_key(name):
    """„Datei 10“ nach „Datei 9“; Groß/klein egal; Umlaute wie im Wörterbuch."""
    parts = _NUM.split(name.casefold())
    out = []
    for p in parts:
        if p.isdigit():
            out.append((0, int(p), ""))
        elif p:
            out.append((1, 0, locale.strxfrm(p) if _LOCALE_OK else p))
    return out


try:
    locale.setlocale(locale.LC_COLLATE, "")
    _LOCALE_OK = True
except locale.Error:
    _LOCALE_OK = False

_DESC = {}


def type_description(ctype):
    d = _DESC.get(ctype)
    if d is None:
        d = _DESC[ctype] = Gio.content_type_get_description(ctype) if ctype else ""
    return d


class FileItem(GObject.Object):
    """Ein Eintrag in einer Liste. Felder sind normale Python-Attribute (schnell), kein GObject-Property."""
    __gtype_name__ = "FwFileItem"

    def __init__(self, name, uri, path, is_dir, is_link, size, mtime, mode, ctype, display=None, extra=None,
                 meta=None):
        super().__init__()
        self.name = name                # Dateiname
        self.display = display or name  # angezeigt (z. B. Papierkorb: Originalname)
        self.uri = uri
        self.path = path                # lokaler Pfad oder None
        self.is_dir = is_dir
        self.is_link = is_link
        self.size = size
        self.mtime = mtime
        self.mode = mode
        self.ctype = ctype              # Inhaltstyp (MIME)
        self.extra = extra or {}        # z. B. Papierkorb: orig_path, deleted; Suche: rel
        if meta is None:
            meta = item_meta(name, self.display, is_dir, is_link, mode)
        self.hidden, self.glyph, self.group, self.key = meta
        self.thumb = None               # Gdk.Texture, sobald geladen
        self.dir_count = None           # Anzahl Einträge (Ordner), nachgeladen

    @property
    def gfile(self):
        return Gio.File.new_for_uri(self.uri)

    @property
    def type_text(self):
        if self.is_dir:
            return "Ordner"
        return type_description(self.ctype)

    def size_text(self):
        if self.is_dir:
            return "" if self.dir_count is None else f"{self.dir_count} Elem."
        return human_size(self.size)

    def date_text(self):
        return human_date(self.mtime)


def human_size(n):
    if n is None:
        return ""
    if n < 1000:
        return f"{n} B"
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        n /= 1000
        if n < 1000 or unit == "PB":
            s = f"{n:.1f}" if n < 100 else f"{n:.0f}"
            return s.replace(".", ",") + " " + unit
    return ""


def human_date(ts):
    if not ts:
        return ""
    d = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    if d.date() == now.date():
        return f"heute {d:%H:%M}"
    if (now.date() - d.date()).days == 1:
        return f"gestern {d:%H:%M}"
    if d.year == now.year:
        return f"{d:%d.%m. %H:%M}"
    return f"{d:%d.%m.%Y}"


def uri_for(path):
    return Gio.File.new_for_path(path).get_uri()


def is_local(uri):
    return uri.startswith("file://")


def path_of(uri):
    """Pfad im Dateisystem; für eingehängte Netzorte (smb://, sftp://) der gvfs-FUSE-Pfad, sonst None."""
    if is_local(uri):
        return Gio.File.new_for_uri(uri).get_path()
    if uri.startswith(("trash:", "recent:", "network:", "computer:", "starred:")) or "://" not in uri:
        return None
    return Gio.File.new_for_uri(uri).get_path()


def item_meta(name, display, is_dir, is_link, mode):
    """Vorberechnetes (versteckt, Glyphe, Gruppe, Sortierschlüssel) – läuft gern im Hintergrund-Thread."""
    exe = bool(mode & 0o111) and not is_dir if mode else False
    glyph, group = icons.for_name(name, is_dir, is_link, exe)
    return icons.is_hidden(name), glyph, group, natural_key(display)


# ---------------------------------------------------------------- Einlesen
def _scan_local(path):
    """Läuft im Thread. Liefert eine Liste von Tupeln (schnell zu erzeugen, GObjects baut der GTK-Thread)."""
    out = []
    guess = Gio.content_type_guess
    with os.scandir(path) as it:
        for e in it:
            name = e.name
            try:
                st = e.stat()                       # folgt Links
                is_link = e.is_symlink()
            except OSError:
                try:
                    st = e.stat(follow_symlinks=False)
                    is_link = True
                except OSError:
                    continue
            is_dir = stat.S_ISDIR(st.st_mode)
            ctype = "inode/directory" if is_dir else guess(name, None)[0]
            full = os.path.join(path, name)
            out.append((name, GLib.filename_to_uri(full, None), full, is_dir, is_link, st.st_size, st.st_mtime,
                        st.st_mode, ctype, item_meta(name, name, is_dir, is_link, st.st_mode)))
    return out


GIO_ATTRS = ("standard::name,standard::display-name,standard::type,standard::size,standard::content-type,"
             "standard::is-symlink,standard::is-hidden,time::modified,unix::mode,trash::orig-path,"
             "trash::deletion-date,standard::target-uri")


def _scan_gio(uri, cancellable):
    out = []
    d = Gio.File.new_for_uri(uri)
    en = d.enumerate_children(GIO_ATTRS, Gio.FileQueryInfoFlags.NONE, cancellable)
    while True:
        info = en.next_file(cancellable)
        if info is None:
            break
        if True:
            child = d.get_child(info.get_name())
            target = info.get_attribute_string("standard::target-uri")
            is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
            extra = {}
            orig = info.get_attribute_byte_string("trash::orig-path")
            if orig:
                extra["orig_path"] = orig
                dd = info.get_attribute_string("trash::deletion-date")
                extra["deleted"] = dd or ""
            out.append(dict(name=info.get_name(), display=info.get_display_name(), uri=child.get_uri(),
                            target=target, is_dir=is_dir, is_link=info.get_is_symlink(), size=info.get_size(),
                            mtime=info.get_attribute_uint64("time::modified") or 0,
                            mode=info.get_attribute_uint32("unix::mode") or 0,
                            ctype=info.get_content_type() or "", extra=extra))
    en.close(None)
    return out


class Loader:
    """Liest einen Ordner im Hintergrund und ruft done(items, error) im GTK-Thread auf.
    Ein neuer load() bricht einen laufenden ab (Ergebnis wird verworfen)."""

    def __init__(self):
        self.gen = 0
        self.cancel = None

    def load(self, uri, done):
        self.gen += 1
        gen = self.gen
        if self.cancel:
            self.cancel.cancel()
        self.cancel = Gio.Cancellable()
        cancel = self.cancel

        def work():
            t0 = time.monotonic()
            try:
                if is_local(uri):
                    raw = _scan_local(path_of(uri))
                    items, err = ("local", raw), None
                else:
                    items, err = ("gio", _scan_gio(uri, cancel)), None
            except (OSError, GLib.Error) as e:
                items, err = None, e
            GLib.idle_add(lambda: self._deliver(gen, items, err, time.monotonic() - t0) and False)
        self.done = done
        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, gen, items, err, took):
        if gen != self.gen:
            return False
        if err is not None:
            self.done(None, err)
            return False
        kind, raw = items
        if kind == "local":
            out = [FileItem(n, u, p, d, l, s, m, mo, c, meta=x) for n, u, p, d, l, s, m, mo, c, x in raw]
        else:
            out = []
            for r in raw:
                path = path_of(r["target"] or r["uri"])
                display = r["display"]
                if r["extra"].get("orig_path"):
                    display = os.path.basename(r["extra"]["orig_path"])
                it = FileItem(r["name"], r["target"] or r["uri"], path, r["is_dir"], r["is_link"], r["size"],
                              r["mtime"], r["mode"], r["ctype"], display=display, extra=r["extra"])
                it.extra["gio_uri"] = r["uri"]
                out.append(it)
        self.done(out, None)
        return False


# Schneller als Gio.File.get_uri() für viele Einträge, aber gleiches Ergebnis
def fast_uri(path):
    return GLib.filename_to_uri(path, None)


def sort_items(items, field="name", desc=False, folders_first=True):
    if field == "size":
        k = lambda i: (i.size if not i.is_dir else (i.dir_count or 0), i.key)
    elif field == "mtime":
        k = lambda i: (i.mtime, i.key)
    elif field == "type":
        k = lambda i: (i.type_text.casefold(), i.key)
    elif field == "deleted":
        k = lambda i: (i.extra.get("deleted", ""), i.key)
    else:
        k = lambda i: i.key
    items.sort(key=k, reverse=desc)
    if folders_first:
        items.sort(key=lambda i: not i.is_dir)      # stabil: Reihenfolge innerhalb bleibt
    return items


_df_cache = {}          # path → (free, total)
_df_busy = {}           # path → [callbacks] während statvfs im Hintergrund läuft


def disk_free(path, on_done=None, wait=0.05):
    """(frei, gesamt) in Bytes. statvfs läuft im Hintergrund: Netzlaufwerke (SMB über VPN, gvfs-FUSE)
    brauchen dafür teils Sekunden. Dauert es länger als `wait`, kommt der letzte bekannte Wert (oder
    None, None) zurück und `on_done(free, total)` wird später im GTK-Thread aufgerufen."""
    if path in _df_busy:
        if on_done:
            _df_busy[path].append(on_done)
        return _df_cache.get(path, (None, None))
    ev = threading.Event()
    _df_busy[path] = [on_done] if on_done else []

    def work():
        try:
            st = os.statvfs(path)
            res = (st.f_bavail * st.f_frsize, st.f_blocks * st.f_frsize)
        except OSError:
            res = (None, None)
        _df_cache[path] = res
        ev.set()

        def deliver():
            for cb in _df_busy.pop(path, []):
                if not ev.quick:
                    cb(*res)
            return False
        GLib.idle_add(deliver)

    ev.quick = False
    threading.Thread(target=work, daemon=True).start()
    if ev.wait(wait):
        ev.quick = True             # schnell genug: Ergebnis direkt, kein Rückruf nötig
        return _df_cache[path]
    return _df_cache.get(path, (None, None))


# ---------------------------------------------------------------- Suchfilter
# Suchfeld: normaler Text = Namensteil (auch mit * ? [ ]); dazu Filter, mit Leerzeichen getrennt:
#   typ:bild|video|audio|dokument|archiv|code|ordner|datei   ext:pdf,docx   größe:>10M  größe:1k-5M
#   geändert:<7d (neuer als 7 Tage)  geändert:>1y (älter als ein Jahr)  geändert:heute   (d/w/m/y/h)
_KINDS = {
    "bild": ("image/",), "image": ("image/",), "foto": ("image/",),
    "video": ("video/",), "audio": ("audio/",), "musik": ("audio/",),
    "text": ("text/",),
    "archiv": ("application/zip", "application/x-tar", "application/x-7z", "application/x-compressed",
               "application/gzip", "application/x-xz", "application/x-bzip", "application/x-rar",
               "application/vnd.rar", "application/zstd", "application/x-zstd"),
    "dokument": ("application/pdf", "application/msword", "application/vnd.", "application/rtf",
                 "application/x-tex", "application/epub", "text/plain", "text/markdown", "text/rtf"),
    "code": ("text/x-", "application/x-shellscript", "application/json", "application/javascript",
             "application/xml", "text/html", "text/css", "text/javascript", "application/x-python"),
}
_KINDS["doc"] = _KINDS["dokument"]
_KINDS["archive"] = _KINDS["archiv"]
_SIZE_UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
_AGE_UNITS = {"h": 3600, "d": 86400, "w": 7 * 86400, "m": 30 * 86400, "y": 365 * 86400}
_KEYS = {"typ": "typ", "type": "typ", "ext": "ext", "endung": "ext", "größe": "size", "grösse": "size", "groesse": "size",
         "size": "size", "geändert": "mod", "geaendert": "mod", "alt": "mod", "mod": "mod", "modified": "mod"}


def _size(txt):
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([kmgtb]?)i?b?", txt.strip().lower())
    if not m:
        raise ValueError(f"größe „{txt}“ verstehe ich nicht (z. B. 10M, >500k, 1k-5M)")
    return float(m.group(1).replace(",", ".")) * _SIZE_UNITS[m.group(2)]


def _age(txt):
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([hdwmy])", txt.strip().lower())
    if not m:
        raise ValueError(f"zeit „{txt}“ verstehe ich nicht (z. B. 7d, <2w, >1y)")
    return float(m.group(1).replace(",", ".")) * _AGE_UNITS[m.group(2)]


def parse_query(query, now=None):
    """Suchtext → (Namensmuster klein, Filterfunktion|None, Fehler|None).
    Filterfunktion(name, is_dir, size, mtime, ctype) → bool."""
    now = now or time.time()
    words, tests = [], []
    try:
        toks = shlex.split(query)
    except ValueError:
        toks = query.split()
    for tok in toks:
        key, sep, val = tok.partition(":")
        key = _KEYS.get(key.lower()) if sep and val else None
        if key is None:
            words.append(tok)
            continue
        val = val.strip()
        try:
            if key == "typ":
                kinds = []
                for v in val.lower().split(","):
                    if v in ("ordner", "dir", "folder"):
                        kinds.append("dir")
                    elif v in ("datei", "file"):
                        kinds.append("file")
                    elif v in _KINDS:
                        kinds.append(v)
                    else:
                        raise ValueError(f"typ „{v}“ unbekannt – bild, video, audio, dokument, archiv, code, text, "
                                         "ordner, datei")

                def t_typ(n, d, s, m, c, kinds=tuple(kinds)):
                    for k in kinds:
                        if k == "dir" and d or k == "file" and not d:
                            return True
                        if k in _KINDS and not d and (c or "").startswith(_KINDS[k]):
                            return True
                    return False
                tests.append(t_typ)
            elif key == "ext":
                exts = {"." + e.lower().lstrip(".") for e in val.split(",") if e.strip(".")}
                tests.append(lambda n, d, s, m, c, exts=exts: not d and os.path.splitext(n.lower())[1] in exts)
            elif key == "size":
                if val[0] in "<>":
                    lim = _size(val[1:])
                    tests.append((lambda n, d, s, m, c, lim=lim: not d and s > lim) if val[0] == ">" else
                                 (lambda n, d, s, m, c, lim=lim: not d and s < lim))
                elif "-" in val:
                    lo, hi = (_size(x) for x in val.split("-", 1))
                    tests.append(lambda n, d, s, m, c, lo=lo, hi=hi: not d and lo <= s <= hi)
                else:
                    lim = _size(val)
                    tests.append(lambda n, d, s, m, c, lim=lim: not d and lim * 0.9 <= s <= lim * 1.1)
            elif key == "mod":
                if val.lower() in ("heute", "today"):
                    t0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
                    tests.append(lambda n, d, s, m, c, t0=t0: m >= t0)
                elif val[0] == ">":          # älter als
                    age = _age(val[1:])
                    tests.append(lambda n, d, s, m, c, age=age: now - m > age)
                else:                        # „<7d“ oder „7d“ = neuer als
                    age = _age(val[1:] if val[0] == "<" else val)
                    tests.append(lambda n, d, s, m, c, age=age: now - m <= age)
        except ValueError as e:
            return "", None, str(e)
    name = " ".join(words).casefold()
    if not tests:
        return name, None, None
    return name, (lambda n, d, s, m, c: all(t(n, d, s, m, c) for t in tests)), None
