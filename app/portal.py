# D-Bus-Dienste von filist:
#  • org.freedesktop.impl.portal.FileChooser – Datei-Dialog („Öffnen“/„Speichern unter“) für alle Programme,
#    die das Portal benutzen (über xdg-desktop-portal, siehe packaging/setup-portal.sh).
#  • org.freedesktop.FileManager1 – „Im Ordner anzeigen“ aus Browsern, Downloads usw.
import os

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

import model

PORTAL_NAME = "org.freedesktop.impl.portal.desktop.filist"
PORTAL_PATH = "/org/freedesktop/portal/desktop"

CHOOSER_XML = """
<node>
 <interface name="org.freedesktop.impl.portal.FileChooser">
  <method name="OpenFile">
   <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
   <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="SaveFile">
   <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
   <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="SaveFiles">
   <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
   <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
   <arg type="a{sv}" name="options" direction="in"/>
   <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <property name="version" type="u" access="read"/>
 </interface>
</node>"""

REQUEST_XML = """
<node><interface name="org.freedesktop.impl.portal.Request"><method name="Close"/></interface></node>"""

FM_XML = """
<node>
 <interface name="org.freedesktop.FileManager1">
  <method name="ShowFolders"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItems"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItemProperties"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
 </interface>
</node>"""


def _bytes_path(v):
    if v is None:
        return None
    b = bytes(v)
    return b.rstrip(b"\0").decode("utf-8", "surrogateescape") or None


def is_default_file_manager():
    try:
        info = Gio.AppInfo.get_default_for_type("inode/directory", False)
        return bool(info) and info.get_id() == "filist.desktop"
    except Exception:
        return False


def _readable(uri):
    """Netzorte (smb://, sftp://) als file://-URI des gvfs-FUSE-Pfads – nur den können andere Programme lesen."""
    if uri.startswith("file://"):
        return uri
    p = model.path_of(uri)
    return model.uri_for(p) if p else uri


class Services:
    def __init__(self, app):
        self.app = app
        self.conn = app.get_dbus_connection()
        self.requests = {}
        self.ids = []
        if self.conn is None:
            return
        chooser = Gio.DBusNodeInfo.new_for_xml(CHOOSER_XML).interfaces[0]
        self.request_iface = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML).interfaces[0]
        fm = Gio.DBusNodeInfo.new_for_xml(FM_XML).interfaces[0]
        self.ids.append(self.conn.register_object(PORTAL_PATH, chooser, self._chooser_call, self._get_prop, None))
        self.ids.append(self.conn.register_object("/org/freedesktop/FileManager1", fm, self._fm_call, None, None))
        names = [PORTAL_NAME]
        if is_default_file_manager():   # sonst nicht Nautilus/Dolphin & Co. „Im Ordner anzeigen“ wegnehmen
            names.append("org.freedesktop.FileManager1")
        for name in names:
            Gio.bus_own_name_on_connection(self.conn, name, Gio.BusNameOwnerFlags.REPLACE |
                                           Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT, None, None)

    def _get_prop(self, conn, sender, path, iface, prop):
        if prop == "version":
            return GLib.Variant("u", 4)
        return None

    # ----- FileManager1 -----
    def _fm_call(self, conn, sender, path, iface, method, params, inv):
        uris, _startup = params.unpack()
        inv.return_value(None)
        if not uris:
            return
        if method == "ShowFolders":
            self.app.open_window(uris[0])
            for u in uris[1:]:
                self.app.open_window(u)
        elif method == "ShowItems":
            f = Gio.File.new_for_uri(uris[0])
            parent = f.get_parent()
            win = self.app.open_window(parent.get_uri() if parent else uris[0], select=uris)
        elif method == "ShowItemProperties":
            win = self.app.open_window(Gio.File.new_for_uri(uris[0]).get_parent().get_uri(), select=uris)
            GLib.timeout_add(400, lambda: win.properties() and False)

    # ----- FileChooser -----
    def _chooser_call(self, conn, sender, path, iface, method, params, inv):
        handle, app_id, parent, title, options = params.unpack()
        opts = options
        mode = "open"
        chooser = {"title": title, "app_id": app_id, "parent": parent,
                   "accept_label": opts.get("accept_label"), "multiple": bool(opts.get("multiple")),
                   "directory": bool(opts.get("directory"))}
        filters = []
        for name, rules in opts.get("filters", []) or []:
            filters.append((name, list(rules)))
        chooser["filters"] = filters
        cur = opts.get("current_filter")
        if cur and filters:
            for i, f in enumerate(filters):
                if f[0] == cur[0]:
                    chooser["current_filter_index"] = i
        folder = _bytes_path(opts.get("current_folder"))
        current_file = _bytes_path(opts.get("current_file"))
        if method == "SaveFile":
            mode = "save"
            chooser["current_name"] = opts.get("current_name") or (os.path.basename(current_file) if current_file else "")
            if current_file and not folder:
                folder = os.path.dirname(current_file)
        elif method == "SaveFiles":
            mode = "dir"
            names = [_bytes_path(b) for b in opts.get("files", []) or []]
            chooser["save_files"] = [n for n in names if n]
        elif chooser["directory"]:
            mode = "open"

        def done(ok, uris, filter_idx):
            self._close_request(handle)
            if not ok:
                inv.return_value(GLib.Variant("(ua{sv})", (1, {})))
                return
            if method == "SaveFiles":
                d = Gio.File.new_for_uri(uris[0])
                uris = [d.get_child(n).get_uri() for n in chooser.get("save_files", [])]
            uris = [_readable(u) for u in uris]
            res = {"uris": GLib.Variant("as", uris)}
            if filter_idx is not None and filters:
                name, rules = filters[filter_idx]
                res["current_filter"] = GLib.Variant("(sa(us))", (name, rules))
            if opts.get("choices"):
                res["choices"] = GLib.Variant("a(ss)", [(c[0], c[3]) for c in opts["choices"]])
            inv.return_value(GLib.Variant("(ua{sv})", (0, res)))
        chooser["done"] = done
        start = Gio.File.new_for_path(folder).get_uri() if folder and os.path.isdir(folder) else None
        win = self.app.open_chooser(mode, chooser, start)
        try:
            rid = conn.register_object(handle, self.request_iface,
                                       lambda c, s, p, i, m, pa, iv: (iv.return_value(None), win.finish(False)),
                                       None, None)
            self.requests[handle] = rid
        except GLib.Error:
            pass

    def _close_request(self, handle):
        rid = self.requests.pop(handle, None)
        if rid:
            try:
                self.conn.unregister_object(rid)
            except GLib.Error:
                pass
