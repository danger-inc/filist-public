# Datei-Operationen mit Fortschritt, Abbrechen, Konfliktabfrage und Rückgängig.
# Jede größere Aufgabe (Kopieren, Verschieben, Löschen, Packen, Entpacken …) läuft in einem eigenen Thread;
# die Oberfläche bekommt höchstens ~12 Updates pro Sekunde. Konflikte fragt der Thread über ask() im
# GTK-Thread nach und wartet auf die Antwort.
import os, shutil, signal, subprocess, tarfile, threading, time, zipfile

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

import model

COPY_FLAGS = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, ops, kind, title):
        self.ops, self.kind, self.title = ops, kind, title
        self.cancel = Gio.Cancellable()
        self.total_bytes = 0        # 0 = unbekannt
        self.done_bytes = 0
        self.total_files = 0
        self.done_files = 0
        self.current = ""
        self.state = "läuft"        # läuft / fertig / fehler / abgebrochen
        self.error = None
        self.errors = []            # (pfad, meldung) – nicht abbrechende Fehler
        self.decision = {}          # „für alle“-Antworten je Konfliktart
        self.undo = None
        self.started = time.monotonic()
        self._queued = False
        self.proc = None

    # ----- Fortschritt -----
    @property
    def fraction(self):
        if self.total_bytes:
            return min(1.0, self.done_bytes / self.total_bytes)
        if self.total_files:
            return min(1.0, self.done_files / self.total_files)
        return None

    def speed(self):
        t = time.monotonic() - self.started
        return self.done_bytes / t if t > 0.5 and self.done_bytes else 0

    def touch(self):
        if not self._queued:
            self._queued = True
            GLib.timeout_add(80, self._emit)

    def _emit(self):
        self._queued = False
        self.ops.changed(self)
        return False

    def check(self):
        if self.cancel.is_cancelled():
            raise Cancelled()

    def stop(self):
        self.cancel.cancel()
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass

    # ----- Rückfrage im GTK-Thread -----
    def ask(self, kind, src, dst):
        """Konflikt: Antwort 'replace' | 'skip' | 'rename' | 'merge' | 'cancel'."""
        if kind in self.decision:
            return self.decision[kind]
        ev = threading.Event()
        box = {}

        def reply(choice, for_all=False):
            box["c"] = choice
            if for_all and choice != "cancel":
                self.decision[kind] = choice
            ev.set()
        GLib.idle_add(lambda: self.ops.on_conflict(self, kind, src, dst, reply) and False)
        while not ev.wait(0.2):
            if self.cancel.is_cancelled():
                return "cancel"
        return box["c"]


class Ops:
    """Verwaltet laufende Aufgaben. on_conflict(job, kind, src, dst, reply) und listeners setzt die UI."""

    def __init__(self):
        self.jobs = []
        self.listeners = []          # fn(job)
        self.on_conflict = lambda job, kind, src, dst, reply: reply("skip")
        self.on_error = lambda job, msg: None
        self.undo_stack = []         # [(titel, rückgängig-funktion)]

    def changed(self, job):
        for fn in list(self.listeners):
            fn(job)

    def _run(self, job, fn, done=None):
        self.jobs.append(job)
        self.changed(job)

        def work():
            try:
                fn(job)
                job.state = "fertig"
            except Cancelled:
                job.state = "abgebrochen"
            except (OSError, GLib.Error, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError) as e:
                job.state = "fehler"
                job.error = _msg(e)
            GLib.idle_add(lambda: self._finish(job, done) and False)
        threading.Thread(target=work, daemon=True).start()
        return job

    def _finish(self, job, done):
        if job.undo and job.state in ("fertig", "abgebrochen"):
            self.undo_stack.append(job.undo)
            del self.undo_stack[:-50]
        if job.state == "fehler":
            self.on_error(job, job.error)
        elif job.errors:
            self.on_error(job, f"{len(job.errors)} Fehler, z. B. {os.path.basename(job.errors[0][0])}: "
                               f"{job.errors[0][1]}")
        self.changed(job)
        if done:
            done(job)
        GLib.timeout_add(2500 if job.state == "fertig" else 8000, lambda: self._drop(job) and False)
        return False

    def _drop(self, job):
        if job in self.jobs:
            self.jobs.remove(job)
            self.changed(job)

    def busy(self):
        return [j for j in self.jobs if j.state == "läuft"]

    # ================================================================ Kopieren / Verschieben
    def transfer(self, uris, dest_dir, move=False, done=None):
        n = len(uris)
        title = ("Verschiebe " if move else "Kopiere ") + (_base(uris[0]) if n == 1 else f"{n} Elemente")
        job = Job(self, "move" if move else "copy", title)
        dest = Gio.File.new_for_uri(dest_dir)
        pairs = []                      # für Rückgängig

        def run(job):
            srcs = [Gio.File.new_for_uri(u) for u in uris]
            # Ziel in sich selbst?
            for s in srcs:
                if dest.equal(s) or dest.has_prefix(s):
                    raise OSError(f"„{s.get_basename()}“ kann nicht in sich selbst {'verschoben' if move else 'kopiert'} werden")
            job.current = "Größe ermitteln …"
            job.touch()
            same_fs = move and all(_same_fs(s, dest) for s in srcs)
            if not same_fs:
                for s in srcs:
                    b, f = _measure(s, job)
                    job.total_bytes += b
                    job.total_files += f
            else:
                job.total_files = len(srcs)
            for s in srcs:
                job.check()
                target = dest.get_child(s.get_basename())
                if move and s.equal(target):
                    continue
                if not move and s.equal(target):     # Kopie im selben Ordner → „name (Kopie)“
                    target = _free_name(dest, s.get_basename(), copy=True)
                res = self._place(job, s, target, move, same_fs)
                if res:
                    pairs.append((s.get_uri(), res.get_uri()))
            if pairs:
                if move:
                    job.undo = (f"Verschieben von {len(pairs)} Element(en)", lambda: self.transfer_back(pairs))
                else:
                    job.undo = (f"Kopieren von {len(pairs)} Element(en)",
                                lambda: self.trash([d for _s, d in pairs], record=False))
        return self._run(job, run, done)

    def transfer_back(self, pairs):
        """Rückgängig für Verschieben: alles an den alten Ort zurück."""
        job = Job(self, "move", f"Zurückverschieben ({len(pairs)})")

        def run(job):
            for src, dst in pairs:
                job.check()
                s, d = Gio.File.new_for_uri(dst), Gio.File.new_for_uri(src)
                try:
                    _move_file(job, s, d, overwrite=False)
                except GLib.Error as e:
                    job.errors.append((dst, _msg(e)))
        return self._run(job, run)

    def _place(self, job, src, target, move, same_fs):
        """Eine Datei/einen Ordner nach target bringen. Gibt das tatsächliche Ziel zurück (oder None = übersprungen)."""
        job.check()
        job.current = src.get_basename()
        job.touch()
        src_dir = _is_dir(src)
        if target.query_exists(job.cancel):
            tgt_dir = _is_dir(target)
            kind = "dir" if (src_dir and tgt_dir) else "file"
            choice = job.ask(kind, src, target)
            if choice == "cancel":
                raise Cancelled()
            if choice == "skip":
                b, f = (_measure(src, job) if not same_fs else (0, 1))
                job.done_bytes += b
                job.done_files += f
                return None
            if choice == "rename":
                target = _free_name(target.get_parent(), target.get_basename())
            elif choice == "merge" and src_dir and tgt_dir:
                for child in _children(src, job):
                    self._place(job, child, target.get_child(child.get_basename()), move, same_fs)
                if move:
                    _delete_if_empty(src)
                return target
            elif choice == "replace":
                if tgt_dir and not src_dir or src_dir and not tgt_dir:
                    _delete_tree(target, job)
                elif tgt_dir:
                    _delete_tree(target, job)
        if move and same_fs:
            _move_file(job, src, target, overwrite=True)
            job.done_files += 1
            job.touch()
            return target
        if src_dir:
            target.make_directory(job.cancel)
            job.done_files += 1
            try:
                _copy_attrs(src, target)
            except GLib.Error:
                pass
            for child in _children(src, job):
                self._place(job, child, target.get_child(child.get_basename()), move, same_fs)
            if move:
                _delete_if_empty(src)
            return target
        _copy_file(job, src, target)
        if move:
            try:
                src.delete(job.cancel)
            except GLib.Error as e:
                job.errors.append((src.get_uri(), _msg(e)))
        return target

    # ================================================================ Papierkorb / Löschen
    def trash(self, uris, record=True, done=None):
        n = len(uris)
        job = Job(self, "trash", "In den Papierkorb: " + (_base(uris[0]) if n == 1 else f"{n} Elemente"))
        moved = []

        def run(job):
            job.total_files = n
            for u in uris:
                job.check()
                f = Gio.File.new_for_uri(u)
                job.current = f.get_basename()
                job.touch()
                try:
                    f.trash(job.cancel)
                    moved.append(f.get_path() or u)
                except GLib.Error as e:
                    if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED):
                        raise OSError(f"„{f.get_basename()}“ liegt auf einem Laufwerk ohne Papierkorb – "
                                      "endgültig löschen mit Umschalt+Entf")
                    job.errors.append((u, _msg(e)))
                job.done_files += 1
            if record and moved:
                job.undo = (f"Papierkorb ({len(moved)})", lambda: self.restore_paths(moved))
        return self._run(job, run, done)

    def delete(self, uris, done=None):
        n = len(uris)
        job = Job(self, "delete", "Endgültig löschen: " + (_base(uris[0]) if n == 1 else f"{n} Elemente"))

        def run(job):
            job.total_files = n
            for u in uris:
                job.check()
                f = Gio.File.new_for_uri(u)
                job.current = f.get_basename()
                job.touch()
                _delete_tree(f, job)
                job.done_files += 1
        return self._run(job, run, done)

    def restore_paths(self, orig_paths):
        """Aus dem Papierkorb zurückholen (Rückgängig für „in den Papierkorb“)."""
        wanted = set(orig_paths)
        job = Job(self, "restore", f"Wiederherstellen ({len(wanted)})")

        def run(job):
            trash = Gio.File.new_for_uri("trash:///")
            en = trash.enumerate_children("standard::name,trash::orig-path,trash::deletion-date",
                                          Gio.FileQueryInfoFlags.NONE, job.cancel)
            best = {}
            for info in en:
                orig = info.get_attribute_byte_string("trash::orig-path")
                if orig in wanted:
                    dd = info.get_attribute_string("trash::deletion-date") or ""
                    if orig not in best or dd > best[orig][1]:
                        best[orig] = (trash.get_child(info.get_name()), dd)
            job.total_files = len(best)
            for orig, (tf, _dd) in best.items():
                job.check()
                dst = Gio.File.new_for_path(orig)
                job.current = dst.get_basename()
                job.touch()
                if dst.query_exists(None):
                    dst = _free_name(dst.get_parent(), dst.get_basename())
                try:
                    parent = dst.get_parent()
                    if parent and not parent.query_exists(None):
                        parent.make_directory_with_parents(None)
                    tf.move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, job.cancel, None, None)
                except GLib.Error as e:
                    job.errors.append((orig, _msg(e)))
                job.done_files += 1
        return self._run(job, run)

    def restore_items(self, items, done=None):
        """Papierkorb-Einträge (FileItem mit extra['orig_path']) an ihren Ursprungsort."""
        job = Job(self, "restore", f"Wiederherstellen ({len(items)})")
        pairs = [(it.extra.get("gio_uri") or it.uri, it.extra.get("orig_path")) for it in items]

        def run(job):
            job.total_files = len(pairs)
            for src, orig in pairs:
                job.check()
                if not orig:
                    continue
                dst = Gio.File.new_for_path(orig)
                job.current = dst.get_basename()
                job.touch()
                if dst.query_exists(None):
                    choice = job.ask("file", Gio.File.new_for_uri(src), dst)
                    if choice == "cancel":
                        raise Cancelled()
                    if choice == "skip":
                        continue
                    if choice == "replace":
                        _delete_tree(dst, job)
                    else:
                        dst = _free_name(dst.get_parent(), dst.get_basename())
                parent = dst.get_parent()
                if parent and not parent.query_exists(None):
                    parent.make_directory_with_parents(None)
                Gio.File.new_for_uri(src).move(dst, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, job.cancel, None, None)
                job.done_files += 1
        return self._run(job, run, done)

    def empty_trash(self, done=None):
        job = Job(self, "delete", "Papierkorb leeren")

        def run(job):
            trash = Gio.File.new_for_uri("trash:///")
            kids = list(_children(trash, job))
            job.total_files = len(kids)
            for k in kids:
                job.check()
                job.current = k.get_basename()
                job.touch()
                _delete_tree(k, job)
                job.done_files += 1
        return self._run(job, run, done)

    # ================================================================ Archive
    def extract(self, uris, dest_dir, done=None):
        job = Job(self, "extract", "Entpacke " + (_base(uris[0]) if len(uris) == 1 else f"{len(uris)} Archive"))

        def run(job):
            job.total_files = len(uris)
            for u in uris:
                job.check()
                path = model.path_of(u)
                if not path:
                    raise OSError("Nur lokale Archive lassen sich entpacken")
                job.current = os.path.basename(path)
                job.touch()
                base = _archive_stem(os.path.basename(path))
                tmp = os.path.join(model.path_of(dest_dir), f".{base}.entpacken-{os.getpid()}")
                os.makedirs(tmp, exist_ok=True)
                try:
                    _run_tool(job, _extract_cmd(path, tmp))
                    entries = os.listdir(tmp)
                    # ein einziger Ordner im Archiv → direkt den nehmen, sonst Ordner mit Archivnamen
                    if len(entries) == 1 and os.path.isdir(os.path.join(tmp, entries[0])):
                        src, name = os.path.join(tmp, entries[0]), entries[0]
                    else:
                        src, name = tmp, base
                    final = _free_path(os.path.join(model.path_of(dest_dir), name))
                    os.rename(src, final)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                job.done_files += 1
            job.undo = None
        return self._run(job, run, done)

    def compress(self, uris, out_path, fmt, done=None):
        job = Job(self, "compress", "Packe " + os.path.basename(out_path))
        paths = [model.path_of(u) for u in uris]

        def run(job):
            if not all(paths):
                raise OSError("Nur lokale Dateien lassen sich packen")
            base = os.path.dirname(paths[0])
            files = []
            for p in paths:
                if os.path.isdir(p) and not os.path.islink(p):
                    for root, dirs, fs in os.walk(p):
                        files.append(root)
                        files.extend(os.path.join(root, f) for f in fs)
                else:
                    files.append(p)
            job.total_files = len(files)
            job.total_bytes = sum(os.path.getsize(f) for f in files if os.path.isfile(f) and not os.path.islink(f))
            tmp = out_path + ".teil"
            try:
                if fmt == "zip":
                    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                        for f in files:
                            job.check()
                            job.current = os.path.relpath(f, base)
                            z.write(f, os.path.relpath(f, base))
                            job.done_files += 1
                            if os.path.isfile(f):
                                job.done_bytes += os.path.getsize(f)
                            job.touch()
                elif fmt in ("tar.gz", "tar.xz", "tar.bz2", "tar"):
                    mode = {"tar.gz": "w:gz", "tar.xz": "w:xz", "tar.bz2": "w:bz2", "tar": "w"}[fmt]
                    with tarfile.open(tmp, mode) as t:
                        for f in files:
                            job.check()
                            job.current = os.path.relpath(f, base)
                            t.add(f, os.path.relpath(f, base), recursive=False)
                            job.done_files += 1
                            if os.path.isfile(f):
                                job.done_bytes += os.path.getsize(f)
                            job.touch()
                elif fmt == "7z":
                    seven = shutil.which("7z") or shutil.which("7zz")
                    if not seven:
                        raise OSError("7z ist nicht installiert")
                    job.total_bytes = 0
                    _run_tool(job, [seven, "a", "-bd", "-y", tmp + ".7z"] + [os.path.relpath(p, base) for p in paths],
                              cwd=base)
                    os.rename(tmp + ".7z", tmp)
                os.rename(tmp, out_path)
            finally:
                for t in (tmp, tmp + ".7z"):
                    if os.path.exists(t):
                        os.remove(t)
            job.undo = (f"Packen von {os.path.basename(out_path)}", lambda: self.trash([model.uri_for(out_path)],
                                                                                    record=False))
        return self._run(job, run, done)

    # ================================================================ kleine, sofortige Aktionen
    def record(self, title, undo_fn):
        self.undo_stack.append((title, undo_fn))
        del self.undo_stack[:-50]

    def undo(self):
        if not self.undo_stack:
            return None
        title, fn = self.undo_stack.pop()
        fn()
        return title


# ---------------------------------------------------------------- Hilfen (laufen im Thread)
def _msg(e):
    if isinstance(e, GLib.Error):
        return e.message
    if isinstance(e, OSError) and e.strerror:
        return e.strerror + (f": {os.path.basename(e.filename)}" if e.filename else "")
    return str(e)


def _base(uri):
    return Gio.File.new_for_uri(uri).get_basename()


def _is_dir(f):
    try:
        return f.query_file_type(Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None) == Gio.FileType.DIRECTORY
    except GLib.Error:
        return False


def _children(f, job):
    en = f.enumerate_children("standard::name", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, job.cancel)
    out = [f.get_child(i.get_name()) for i in en]
    en.close(None)
    return out


def _same_fs(a, b):
    try:
        ia = a.query_info("id::filesystem", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
        ib = b.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, None)
        return ia.get_attribute_string("id::filesystem") == ib.get_attribute_string("id::filesystem")
    except GLib.Error:
        return False


def _measure(f, job):
    """(Bytes, Dateien) – lokal per os.walk, sonst über Gio."""
    p = f.get_path()
    if p:
        if os.path.islink(p) or not os.path.isdir(p):
            try:
                return os.lstat(p).st_size, 1
            except OSError:
                return 0, 1
        total, count = 0, 0
        for root, dirs, files in os.walk(p):
            job.check()
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
            count += len(files) + 1
        return total, count
    try:
        info = f.query_info("standard::size,standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, job.cancel)
        if info.get_file_type() != Gio.FileType.DIRECTORY:
            return info.get_size(), 1
        total, count = 0, 1
        for c in _children(f, job):
            b, n = _measure(c, job)
            total += b
            count += n
        return total, count
    except GLib.Error:
        return 0, 1


def _copy_file(job, src, dst):
    start = job.done_bytes

    def progress(cur, total, *_):
        job.done_bytes = start + cur
        job.touch()
    try:
        src.copy(dst, COPY_FLAGS | Gio.FileCopyFlags.OVERWRITE, job.cancel, progress, None)
    except GLib.Error as e:
        if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
            try:
                dst.delete(None)          # halbe Datei nicht liegen lassen
            except GLib.Error:
                pass
            raise Cancelled()
        raise
    try:
        size = src.query_info("standard::size", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None).get_size()
    except GLib.Error:
        size = 0
    job.done_bytes = start + size
    job.done_files += 1
    job.touch()


def _move_file(job, src, dst, overwrite=True):
    flags = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA
    if overwrite:
        flags |= Gio.FileCopyFlags.OVERWRITE
    src.move(dst, flags, job.cancel, None, None)


def _copy_attrs(src, dst):
    info = src.query_info("unix::mode,time::modified", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
    dst.set_attributes_from_info(info, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)


def _delete_tree(f, job):
    p = f.get_path()
    if p and os.path.lexists(p):
        if os.path.isdir(p) and not os.path.islink(p):
            try:                            # Python ≥ 3.12
                shutil.rmtree(p, onexc=lambda fn, path, exc: job.errors.append((path, _msg(exc))))
            except TypeError:               # ältere Versionen (z. B. bei Freunden mit 3.11)
                shutil.rmtree(p, onerror=lambda fn, path, exc: job.errors.append((path, _msg(exc[1]))))
        else:
            os.remove(p)
        return
    if _is_dir(f):
        for c in _children(f, job):
            job.check()
            _delete_tree(c, job)
    f.delete(job.cancel)


def _delete_if_empty(f):
    try:
        f.delete(None)
    except GLib.Error:
        pass


def _free_name(parent, name, copy=False):
    """„name (2).ext“ – ein Name, der im Ordner noch frei ist."""
    stem, ext = _split_ext(name)
    n = 2
    first = f"{stem} (Kopie){ext}" if copy else None
    if first:
        cand = parent.get_child(first)
        if not cand.query_exists(None):
            return cand
    while True:
        cand = parent.get_child(f"{stem} ({'Kopie ' if copy else ''}{n}){ext}")
        if not cand.query_exists(None):
            return cand
        n += 1


def _free_path(path):
    if not os.path.lexists(path):
        return path
    f = _free_name(Gio.File.new_for_path(os.path.dirname(path)), os.path.basename(path))
    return f.get_path()


def _split_ext(name):
    for multi in (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"):
        if name.lower().endswith(multi):
            return name[:-len(multi)], name[-len(multi):]
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    i = name.rfind(".")
    return (name[:i], name[i:]) if i > 0 else (name, "")


ARCHIVE_EXT = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".7z", ".rar",
               ".gz", ".xz", ".bz2", ".zst", ".iso", ".cab", ".jar", ".deb", ".rpm", ".apk", ".cpio", ".lz", ".lzma")


def is_archive(name):
    n = name.lower()
    return n.endswith(ARCHIVE_EXT)


def _archive_stem(name):
    n = name.lower()
    for e in sorted(ARCHIVE_EXT, key=len, reverse=True):
        if n.endswith(e):
            return name[:-len(e)] or name
    return name


def _extract_cmd(path, dest):
    n = path.lower()
    seven = shutil.which("7z") or shutil.which("7zz")
    if n.endswith((".7z", ".rar")) and seven:
        return [seven, "x", "-bd", "-y", f"-o{dest}", path]
    tar = shutil.which("bsdtar") or shutil.which("tar")
    if n.endswith((".gz", ".xz", ".bz2", ".zst", ".lz", ".lzma")) and not ".tar" in n and not n.endswith((".tgz", ".txz", ".tbz2")):
        # einzelne komprimierte Datei
        out = os.path.join(dest, _archive_stem(os.path.basename(path)))
        tool = {".gz": "gzip", ".xz": "xz", ".bz2": "bzip2", ".zst": "zstd", ".lz": "lzip", ".lzma": "xz"}
        ext = next(e for e in tool if n.endswith(e))
        return ["sh", "-c", f'{tool[ext]} -dc "$1" > "$2"', "sh", path, out]
    if tar and "bsdtar" in tar:
        return [tar, "-xf", path, "-C", dest]
    if n.endswith(".zip") and shutil.which("unzip"):
        return ["unzip", "-q", "-o", path, "-d", dest]
    if seven:
        return [seven, "x", "-bd", "-y", f"-o{dest}", path]
    return [tar or "tar", "-xf", path, "-C", dest]


def _run_tool(job, cmd, cwd=None):
    job.proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
    while True:
        try:
            _out, err = job.proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if job.cancel.is_cancelled():
                job.stop()
                raise Cancelled()
    if job.proc.returncode != 0:
        msg = (err or b"").decode(errors="replace").strip().splitlines()
        raise OSError(msg[-1] if msg else f"{os.path.basename(cmd[0])} ist fehlgeschlagen")
