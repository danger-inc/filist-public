<!-- gh-header -->
<div align="center">

<img src="app/logo/filist.svg" width="88" alt="filist">

# filist

**file explorer for linux – tabs, split view, preview**

<img src="https://img.shields.io/badge/GTK-4-4A86CF?style=flat-square&logo=gtk&logoColor=white" alt="GTK"> <img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="python"> <img src="https://img.shields.io/badge/platform-linux-2E7D6B?style=flat-square&logo=linux&logoColor=white" alt="platform"> <img src="https://img.shields.io/badge/made%20for-wayland%20·%20hyprland-7C6BC4?style=flat-square" alt="made for">

</div>

<p align="center"><img src="docs/filist.png" width="860" alt="filist screenshot"></p>

<p align="center"><!-- gh-note --><sub>tip: the glass button (󰃝, top right) makes the window see-through, like the terminal – needs a compositor with blur, e.g. hyprland.</sub></p>

file explorer for linux (gtk4) in the style of the suite (datist, musicalist, notist, scourge):
fast, with tabs, split view and preview – and, if you want, the file dialog ("open" / "save as")
for **all** programs.

## install

the repository is public – no account needed:

```bash
git clone https://github.com/danger-inc/filist-public ~/filist-src
cd ~/filist-src
./install.sh
```

the installer asks for the system packages (password once) and then two things, both **no** by default:

* **default file manager?** – opening folders and "show in folder" (e.g. from the browser) then go to filist.
* **file dialog for all programs?** – "open" / "save as" then come from filist. a deep change,
only makes sense on hyprland/sway; undo with `./setup-portal.sh --undo`.

without both, filist is just an additional file explorer – your current file manager is left alone.
start it with `filist` or from the application menu. removal: `./uninstall.sh` (also reverts the file dialog).

**hyprland:** `bind = SUPER, E, exec, filist` and, if you want the file dialog, under `env`:
`GDK_DEBUG,portals` and `GTK_USE_PORTAL,1`. firefox/zen additionally:
`about:config` → `widget.use-xdg-desktop-portal.file-picker` = `1`.

## update

```bash
cd ~/filist-src && ./update.sh
```

## runs on

| system | status |
| --- | --- |
| ubuntu 24.04+, linux mint 22+, debian 13 | tested (ubuntu 24.04) |
| fedora 39+ | tested (fedora 44) |
| arch, endeavouros, cachyos, manjaro | tested (arch) |
| openSUSE Tumbleweed | should work (untested) |
| windows / macos | no – filist builds on linux services (gvfs, trash, d-bus, portal) |

required is **gtk 4.12 or newer** (ubuntu 22.04 / mint 21 / debian 12 are too old). with systemd filist runs
as a user service in the background (window is instantly there); without systemd it starts on demand via d-bus.

## what's in there

* **tabs** (ctrl+t, ctrl+w, alt+1–9) and **split view** (F3, tab switches the half, F5/F6 copies/moves across)
* **list** (sortable by column header) and **grid** with thumbnails (images, videos, pdf …), zoom with ctrl ±
* **sidebar:** folders, bookmarks (same as gtk/thunar), drives with usage, mount/eject,
network (sftp/smb/ftp), trash
* **path bar:** click or type with ctrl+L (tab completes), `/` and `~` jump right in
* **filter** while typing, **enter searches all subfolders**; in the list just start typing to jump to a name.
the search takes filters: `typ:bild|video|audio|dokument|archiv|code|ordner`, `ext:pdf,docx`, `größe:>10M`, `größe:1k-5M`,
`geändert:<7d` (newer) / `geändert:>1y` (older) / `geändert:heute` – e.g. `bericht typ:dokument geändert:<30d`
* **copy/cut/paste** – also with other programs (thunar, nautilus, browser); images and text from the
clipboard are pasted as a file
* **drag & drop** (ctrl = copy, shift = move, ctrl+shift = link), also onto folders, tabs, path bar and sidebar
* progress with speed and remaining time, **cancel**, conflict dialog (replace, skip, keep both, merge –
also "for all")
* **undo** (ctrl+z) for rename, move, copy, trash, new
* **rename in the row** (F2, like the windows explorer), **batch rename** with search/replace,
regex, numbering and live preview; new folders/files are named right in the row
* **share** (right click): quick share to your phone (with sharist), mail with attachment, bluetooth, qr code
* **network drives behind a vpn:** right click on the location → "vpn vorher verbinden" – when you open it, filist
connects the vpn itself (networkmanager, eduvpn) and shows a spinner with a cancel button meanwhile
* **trash** with restore, delete permanently (shift+del), empty
* **compress** (zip, tar.gz, tar.xz, 7z) and **extract** (almost all formats)
* **preview** (space): images, text, pdf/video still, folder contents, info
* **properties** (alt+enter): size (whole folder too), change permissions (recursive too), owner, default program,
checksums (md5/sha1/sha256)
* **open with** (all programs, searchable, remember as default, custom command), terminal here (F4)
* hidden files (ctrl+h), select by pattern (ctrl+s), invert (ctrl+i)
* narrow windows: sidebar and columns hide automatically

## keyboard shortcuts

| key | action | key | action |
| --- | --- | --- | --- |
| enter | open | alt+← / → / ↑ | back / forward / up |
| ctrl+t / ctrl+w | tab open / close | backspace | up one folder |
| ctrl+l | enter path | ctrl+f | filter / search |
| F2 | rename | del / shift+del | trash / delete |
| ctrl+c / x / v | copy / cut / paste | ctrl+z | undo |
| ctrl+shift+n | new folder | ctrl+alt+n | new file |
| ctrl+h | hidden files | space | preview |
| F3 | split view | F5 / F6 | copy / move to the other half |
| F4 | terminal here | F9 | sidebar |
| ctrl+1 / ctrl+2 | list / grid | ctrl+d | bookmark |
| alt+enter | properties | ctrl+shift+c | copy path |

## requirements

gtk 4, pygobject. recommended: **tumbler** (thumbnails; videos additionally via ffmpeg), **bsdtar/7z** (extracting),
**gvfs** (trash, network, "recent"), **xdg-desktop-portal** (file dialog). `filist --check` shows
what is missing. the icons are bundled.

## problems?

* **boxes instead of symbols:** `./update.sh` – the icon font comes along and is loaded automatically.
* **`filist: command not found`:** `~/.local/bin` is missing in `PATH` (the installer shows what belongs in
`~/.bashrc`). from the application menu it starts anyway.
* **"show in folder" suddenly opens filist:** run `./install.sh` again and answer no at "default file manager?",
or `xdg-mime default org.gnome.Nautilus.desktop inode/directory` (or dolphin/thunar).
* **file dialog acts up:** `./setup-portal.sh --undo`, log out, log in.

<!-- gh-suite -->
## the -ist suite

all apps share one look (terminal style, colors from your wallpaper via matugen), one installer and the same shortcuts.

| app | what it does |
| --- | --- |
| [scourge](https://github.com/danger-inc/scourge-public) | inspect, edit, record, cut – and installs the others |
| [notist](https://github.com/danger-inc/notist-public) | notes · words · latex |
| [datist](https://github.com/danger-inc/datist) | calendar |
| [mailist](https://github.com/danger-inc/mailist-public) | mail |
| [musicalist](https://github.com/danger-inc/musicalist-public) | youtube music |
| [linguist](https://github.com/danger-inc/linguist) | translator + hover corner |
| [sharist](https://github.com/danger-inc/sharist) | quick share |
| [wallist](https://github.com/danger-inc/wallist) | wallpapers |
| [inquirist](https://github.com/danger-inc/inquirist) | launcher |

dotfiles with the matching hyprland setup: [danger-inc/dotfiles](https://github.com/danger-inc/dotfiles)
