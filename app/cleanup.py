# Aufräum-Werkzeug von filist – die Logik (ohne Gtk): Speicherplatz-Posten, Aufräum-Funde in einem Ordner,
# große Dateien und Sortieren. Nichts hier löscht etwas: die Funktionen sammeln nur Vorschläge, das Fenster
# (cleanupui.py) zeigt sie an und führt nach Bestätigung aus. Nutzerdateien gehen immer in den Papierkorb.
import hashlib, heapq, os, re, shutil, time

HOME = os.path.expanduser("~")
DAY = 86400


def human(n):
    n = float(n)
    for u in ("B", "kB", "MB", "GB", "TB"):
        if n < 1000 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" or n >= 100 else f"{n:.1f} {u}"
        n /= 1000


def disk_use(st):
    """Echter Platzbedarf (Blöcke), nicht die scheinbare Größe – so zählt auch `du`."""
    return getattr(st, "st_blocks", 0) * 512 or st.st_size


def tree_size(path, cancel=lambda: False):
    """Platzbedarf einer Datei oder eines Ordners (Symlinks werden nicht verfolgt)."""
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return disk_use(st)
    total = disk_use(st)
    stack = [path]
    while stack and not cancel():
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    total += disk_use(s)
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
        except OSError:
            pass
    return total


def download_dir():
    try:
        for line in open(os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "user-dirs.dirs")):
            if line.startswith("XDG_DOWNLOAD_DIR"):
                return line.split("=", 1)[1].strip().strip('"').replace("$HOME", HOME)
    except OSError:
        pass
    return os.path.join(HOME, "Downloads")


class Item:
    """Ein Vorschlag: paths werden gelöscht (mode 'delete') oder in den Papierkorb gelegt ('trash')."""
    def __init__(self, title, paths, size, note="", mode="trash", checked=False, cmd=None, group=""):
        self.title, self.paths, self.size, self.note = title, list(paths), size, note
        self.mode, self.checked, self.cmd, self.group = mode, checked, cmd, group


# ======================================================================= Speicherplatz
# Dinge unter ~/.cache, die sich von selbst neu aufbauen → vorausgewählt. Alles andere dort steht ungewählt
# in der Liste; eigene Apps (notes, mailist, …) und Sicherungen tauchen nie auf.
CACHE_SAFE = {"thumbnails", "pip", "pnpm", "go-build", "mesa_shader_cache", "qtshadercache", "fontconfig", "gstreamer-1.0",
              "glycin", "ImageMagick", "tectonic", "mozilla", "zen", "chromium", "google-chrome", "nim", "deno",
              "debuginfod_client", "mpv", "ms-playwright-go", "radv_builtin_shaders", "gnome-desktop-thumbnailer",
              "yarn", "npm", "cargo", "electron", "node-gyp", "ccache", "sccache", "Microsoft", "rclone",
              "QtProject", "flatpak", "gtk-4.0", "mesa_shader_cache_db"}
CACHE_HIDE = {"notes", "mailist", "scourge", "ist", "musicalist", "filist", "wallist", "datist", "linguist", "divist",
              "sharist", "inquirist", "matugen", "claude", "claude-cli-nodejs", "gh", "fish", "current_wallpaper.png"}


def _hidden(name):
    return name in CACHE_HIDE or name.startswith(("ist-", "dotfiles", "fw-", "suite-"))


def children(path):
    try:
        return [os.path.join(path, n) for n in os.listdir(path)]
    except OSError:
        return []


def system_items(progress=None, cancel=lambda: False):
    out = []
    trash = os.path.join(HOME, ".local/share/Trash")
    parts = [os.path.join(trash, d) for d in ("files", "info", "expunged")]
    paths = [c for p in parts for c in children(p)]
    if paths:
        out.append(Item("Papierkorb", paths, sum(tree_size(p, cancel) for p in paths),
                        "endgültig leeren", mode="delete", checked=False, group="papierkorb"))
    dl = download_dir()
    old = []
    cutoff = time.time() - 90 * DAY
    for p in children(dl):
        try:
            if os.lstat(p).st_mtime < cutoff and not os.path.basename(p).startswith("."):
                old.append(p)
        except OSError:
            pass
    if old:
        out.append(Item("alte Downloads", old, sum(tree_size(p, cancel) for p in old),
                        f"älter als 90 Tage · {len(old)} Einträge in {os.path.basename(dl)} · in den Papierkorb",
                        mode="trash", group="downloads"))
    cache = os.path.join(HOME, ".cache")
    kids = [p for p in children(cache) if not _hidden(os.path.basename(p))]
    for i, p in enumerate(sorted(kids)):
        if cancel():
            break
        if progress:
            progress(i, len(kids))
        size = tree_size(p, cancel)
        if size < 1_000_000:
            continue
        name = os.path.basename(p)
        safe = name in CACHE_SAFE
        out.append(Item(f"~/.cache/{name}", [p], size,
                        "baut sich selbst neu auf" if safe else "unbekannt – nur wählen, wenn du weißt, was es ist",
                        mode="delete", checked=safe, group="cache"))
    for p, name, cmd in (("/var/cache/pacman/pkg", "Paket-Cache (pacman)", "sudo paccache -rk1"),
                         ("/var/lib/systemd/coredump", "Absturzberichte", "sudo rm -f /var/lib/systemd/coredump/*")):
        if os.path.isdir(p):
            size = tree_size(p, cancel)
            if size > 5_000_000:
                out.append(Item(name, [], size, "braucht Administratorrechte – Befehl kopieren und im Terminal ausführen",
                                mode="cmd", cmd=cmd, group="system"))
    out.sort(key=lambda it: (it.mode == "cmd", -it.size))
    return out


# ======================================================================= Aufräumen im Ordner
TEMP_RX = re.compile(r"(~|\.bak(-[\w.-]*)?|\.orig|\.rej|\.tmp|\.temp|\.swp|\.swo|\.crdownload|\.part|\.old|\.pyc)$|"
                     r"^(\.DS_Store|Thumbs\.db|desktop\.ini|\.directory|~\$.*|\.~lock\..*#)$", re.I)
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}


def tidy_scan(root, progress=None, cancel=lambda: False):
    """→ [Item] in Gruppen: temp, leere Dateien, leere Ordner, kaputte Links, doppelte Dateien."""
    temp, empty_f, empty_d, broken = [], [], [], []
    by_size = {}
    n = 0
    for base, dirs, names in os.walk(root):
        if cancel():
            break
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (d.startswith(".") and not TEMP_RX.search(d))]
        n += len(names)
        if progress and n % 200 < len(names):
            progress(n, 0)
        for d in list(dirs):
            p = os.path.join(base, d)
            try:
                if os.path.islink(p):
                    if not os.path.exists(p):
                        broken.append(p)
                    dirs.remove(d)
                elif not os.listdir(p):
                    empty_d.append(p)
            except OSError:
                pass
        for nme in names:
            p = os.path.join(base, nme)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if os.path.islink(p):
                if not os.path.exists(p):
                    broken.append(p)
            elif TEMP_RX.search(nme):
                temp.append((p, disk_use(st)))
            elif st.st_size == 0:
                empty_f.append(p)
            else:
                by_size.setdefault(st.st_size, []).append(p)
    out = []
    if temp:
        out.append(Item("Temporäre & Sicherungsdateien", [p for p, _s in temp], sum(s for _p, s in temp),
                        "*~, .bak, .tmp, .swp, .orig, .part, .DS_Store, Thumbs.db …", checked=True, group="temp"))
    if broken:
        out.append(Item("Kaputte Verknüpfungen", broken, 0, "zeigen auf etwas, das es nicht mehr gibt",
                        checked=True, group="broken"))
    if empty_f:
        out.append(Item("Leere Dateien", empty_f, 0, "0 Byte", checked=False, group="empty_f"))
    if empty_d:
        empty_d.sort(key=len, reverse=True)
        out.append(Item("Leere Ordner", empty_d, 0, "ohne jeden Inhalt", checked=True, group="empty_d"))
    cands = [(s, ps) for s, ps in by_size.items() if len(ps) > 1]
    for i, (s, ps) in enumerate(cands):
        if cancel():
            break
        if progress:
            progress(i, len(cands))
        for same in _same_files(ps):
            same.sort(key=lambda p: (os.path.getmtime(p), len(p)))       # die älteste bleibt
            out.append(Item(os.path.basename(same[-1]), same[1:], s * (len(same) - 1),
                            f"gleich wie {os.path.relpath(same[0], root)}", checked=False, group="dups"))
    return out


def _same_files(paths):
    heads = {}
    for p in paths:
        try:
            with open(p, "rb") as f:
                heads.setdefault(f.read(65536), []).append(p)
        except OSError:
            pass
    for same in heads.values():
        if len(same) < 2:
            continue
        full = {}
        for p in same:
            h = hashlib.sha256()
            try:
                with open(p, "rb") as f:
                    for b in iter(lambda: f.read(1 << 20), b""):
                        h.update(b)
            except OSError:
                continue
            full.setdefault(h.digest(), []).append(p)
        for g in full.values():
            if len(g) > 1:
                yield g


# ======================================================================= Große Dateien
def big_scan(root, progress=None, cancel=lambda: False, limit=150, min_size=5_000_000):
    """→ (dateien [(größe, pfad, mtime)] absteigend, Unterordner [(größe, pfad)], Gesamtgröße)."""
    heap, per, loose, n = [], {}, 0, 0
    for base, dirs, names in os.walk(root):
        if cancel():
            break
        top = None
        if base != root:
            top = os.path.join(root, os.path.relpath(base, root).split(os.sep)[0])
        for nme in names:
            p = os.path.join(base, nme)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.isfile(p) or os.path.islink(p):
                continue
            size = disk_use(st)
            n += 1
            if top:
                per[top] = per.get(top, 0) + size
            else:
                loose += size
            if size >= min_size:
                item = (size, p, st.st_mtime)
                if len(heap) < limit:
                    heapq.heappush(heap, item)
                else:
                    heapq.heappushpop(heap, item)
        if progress and n % 300 < len(names):
            progress(n, 0)
    files = sorted(heap, reverse=True)
    folders = sorted(((s, p) for p, s in per.items()), reverse=True)
    return files, folders, loose + sum(per.values())


# ======================================================================= Sortieren
KINDS = [("Bilder", "jpg jpeg png gif webp heic heif bmp tif tiff svg raw cr2 nef arw dng ico avif"),
         ("Videos", "mp4 mkv mov avi webm m4v wmv flv 3gp mpg mpeg"),
         ("Musik", "mp3 flac ogg opus m4a wav aac wma mid"),
         ("Dokumente", "pdf doc docx odt rtf txt md tex epub xls xlsx ods csv ppt pptx odp pages numbers key"),
         ("Archive", "zip tar gz tgz bz2 xz 7z rar zst iso img"),
         ("Programme", "exe msi deb rpm appimage apk dmg flatpakref pkg run"),
         ("Code", "py js ts c h cpp rs go java sh lua json yaml yml toml html css sql ipynb")]
EXT_KIND = {e: k for k, exts in KINDS for e in exts.split()}
MODES = {"typ": "nach Typ  (Bilder, Videos, …)", "monat": "nach Monat  (2026-11, …)", "typ-jahr": "Typ und Jahr  (Bilder/2026)"}


def kind_of(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return EXT_KIND.get(ext, "Sonstiges")


def sort_plan(root, mode="typ"):
    """Nur lose Dateien direkt im Ordner (keine Unterordner, nichts Verstecktes) → [(quelle, zielordner)]."""
    plan = []
    for nme in sorted(os.listdir(root)):
        p = os.path.join(root, nme)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if nme.startswith(".") or os.path.isdir(p) or os.path.islink(p):
            continue
        t = time.localtime(st.st_mtime)
        if mode == "monat":
            sub = time.strftime("%Y-%m", t)
        elif mode == "typ-jahr":
            sub = os.path.join(kind_of(nme), time.strftime("%Y", t))
        else:
            sub = kind_of(nme)
        plan.append((p, os.path.join(root, sub)))
    return plan


def _free(path):
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.lexists(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"


def apply_moves(plan, cancel=lambda: False):
    """→ (erledigt [(alt, neu)], fehler [text], neu angelegte Ordner)."""
    done, errs, made = [], [], []
    for src, dstdir in plan:
        if cancel():
            break
        try:
            if os.path.lexists(dstdir) and not os.path.isdir(dstdir):
                raise OSError(f"„{os.path.basename(dstdir)}“ ist schon eine Datei")
            if not os.path.isdir(dstdir):
                top = dstdir
                while not os.path.isdir(os.path.dirname(top)):
                    top = os.path.dirname(top)
                os.makedirs(dstdir)
                made.append(top)
            dst = _free(os.path.join(dstdir, os.path.basename(src)))
            shutil.move(src, dst)
            done.append((src, dst))
        except OSError as e:
            errs.append(f"{os.path.basename(src)}: {e.strerror or e}")
    return done, errs, made


def undo_moves(done, made):
    errs = 0
    for src, dst in reversed(done):
        try:
            shutil.move(dst, _free(src))
        except OSError:
            errs += 1
    for d in sorted(made, key=len, reverse=True):
        for base, _dirs, _n in os.walk(d, topdown=False):
            try:
                os.rmdir(base)                 # nur wenn leer
            except OSError:
                pass
    return errs
