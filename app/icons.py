# Symbole (Nerd-Font / Material Design Icons) und Farbgruppen je Dateityp.
# Glyphen statt Icon-Theme: schnell, scharf in jeder Größe und passend zum Text-Look der Suite.
import os

FOLDER = "\U000F024B"
FOLDER_OPEN = "\U000F0770"
FOLDER_LINK = "\U000F19EE"
FILE = "\U000F0214"
LINK = "\U000F0337"
HOME = "\U000F02DC"
TRASH = "\U000F0A79"
DRIVE = "\U000F02CA"
USB = "\U000F0553"
NETWORK = "\U000F0870"
BOOKMARK = "\U000F00C0"
RECENT = "\U000F02DA"
ROOT = "\U000F0A0A"
SEARCH = "\U000F0349"

# Ordner mit eigenem Symbol (Name im Home-Verzeichnis, auch auf Englisch)
SPECIAL_DIRS = {
    "Desktop": "\U000F01C4", "Schreibtisch": "\U000F01C4",
    "Documents": "\U000F0C82", "Dokumente": "\U000F0C82",
    "Downloads": "\U000F024D",
    "Music": "\U000F1359", "Musik": "\U000F1359",
    "Pictures": "\U000F024F", "Bilder": "\U000F024F",
    "Videos": "\U000F19FA",
    "Projects": "\U000F19FC", "Projekte": "\U000F19FC",
    "Public": "\U000F024C", "Öffentlich": "\U000F024C",
    "Templates": "\U000F0255", "Vorlagen": "\U000F0255",
    ".config": "\U000F107F", ".git": "\U000F02A2",
}

# Endung → (Glyphe, Gruppe). Gruppe = CSS-Klasse für die Farbe.
_EXT = {}


def _reg(glyph, group, *exts):
    for e in exts:
        _EXT[e] = (glyph, group)


_reg("\U000F021F", "img", "png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg", "ico", "heic", "heif",
     "avif", "jxl", "raw", "cr2", "nef", "arw", "dng", "xcf", "psd", "kra")
_reg("\U000F022B", "vid", "mp4", "mkv", "webm", "mov", "avi", "m4v", "wmv", "flv", "mpg", "mpeg", "3gp", "ts", "ogv")
_reg("\U000F0223", "aud", "mp3", "flac", "ogg", "opus", "wav", "m4a", "aac", "wma", "aiff", "alac", "mid", "midi")
_reg("\U000F0226", "doc", "pdf")
_reg("\U000F022C", "doc", "doc", "docx", "odt", "rtf", "pages")
_reg("\U000F021B", "doc", "xls", "xlsx", "ods", "csv", "tsv", "numbers")
_reg("\U000F0227", "doc", "ppt", "pptx", "odp", "key")
_reg("\U000F0219", "txt", "txt", "md", "rst", "log", "nfo", "tex", "bib", "org", "adoc", "notes")
_reg("\U000F003C", "arc", "zip", "tar", "gz", "tgz", "bz2", "xz", "zst", "7z", "rar", "lz", "lzma", "cab", "deb",
     "rpm", "pkg", "apk", "jar", "whl")
_reg("\U000F02CA", "arc", "iso", "img", "dmg", "qcow2", "vdi", "vmdk")
_reg("\U000F0320", "code", "py", "pyw", "ipynb")
_reg("\U000F031E", "code", "js", "mjs", "cjs", "jsx")
_reg("\U000F06E6", "code", "ts", "tsx")
_reg("\U000F1617", "code", "rs")
_reg("\U000F0671", "code", "c", "h", "cpp", "hpp", "cc", "cxx")
_reg("\U000F07D3", "code", "go")
_reg("\U000F0B37", "code", "java", "kt", "kts", "scala")
_reg("\U000F0D2D", "code", "rb")
_reg("\U000F08B1", "code", "lua")
_reg("\U000F022E", "code", "html", "htm", "xml", "css", "scss", "sass", "less", "vue", "svelte", "php", "sql",
     "json", "jsonc", "yaml", "yml", "toml", "ini", "conf", "cfg", "env", "desktop", "service", "lock")
_reg("\U000F018D", "exe", "sh", "bash", "zsh", "fish", "appimage", "run", "bin", "exe", "msi", "bat", "ps1")
_reg("\U000F06D6", "font", "ttf", "otf", "woff", "woff2")
_reg("\U000F0306", "key", "pem", "crt", "key", "pub", "gpg", "asc", "kdbx")
_reg("\U000F05DA", "doc", "epub", "mobi", "azw3", "djvu", "cbz", "cbr")

THUMBABLE = {"img", "vid", "doc"}   # Gruppen, für die es Vorschaubilder gibt (doc: nur pdf/odf über tumbler)


def ext_of(name):
    if name.startswith(".") and name.count(".") == 1:
        return ""
    i = name.rfind(".")
    return name[i + 1:].lower() if i > 0 else ""


def for_name(name, is_dir, is_link=False, executable=False):
    """(Glyphe, Gruppe) für einen Eintrag."""
    if is_dir:
        g = SPECIAL_DIRS.get(name)
        if g:
            return g, "dir"
        return (FOLDER_LINK if is_link else FOLDER), "dir"
    e = ext_of(name)
    hit = _EXT.get(e)
    if hit:
        return hit
    if name in ("Makefile", "Dockerfile", "CMakeLists.txt", "PKGBUILD", "Justfile"):
        return "\U000F022E", "code"
    if name.lower().startswith(("readme", "license", "copying", "changelog")):
        return "\U000F0219", "txt"
    if is_link:
        return LINK, "link"
    if executable:
        return "\U000F018D", "exe"
    return FILE, "file"


def is_hidden(name):
    return name.startswith(".") or name.endswith("~")


def home_relative(path):
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path
