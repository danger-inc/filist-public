# VPN vor Netzlaufwerken: manche Orte (z. B. das Uni-Laufwerk) sind nur über ein VPN erreichbar.
# Für solche Orte merkt sich filist eine VPN-Verbindung des NetworkManagers und baut sie beim Öffnen
# automatisch auf – erst danach wird eingehängt. Regeln stehen in prefs["vpn"] (nicht im Code):
#   [{"host": "laufwerk.uni.example", "nm": "eduVPN", "cmd": ["eduvpn-cli", "-y", "connect", "-n", "1"]}]
# „host“ passt auf den Server selbst und alles darunter (Endung); „cmd“ ist optional und läuft statt
# „nmcli con up“ – bei eduVPN nötig, weil dessen Zertifikat nach ein paar Stunden abläuft und nur der
# eduVPN-Client ein neues holt (meist ohne Rückfrage, sonst einmal im Browser anmelden).
import os, re, shutil, subprocess, threading, time, urllib.parse

from gi.repository import GLib


def host_of(uri):
    """Server eines Orts: smb://server/…, aber auch der gvfs-Pfad /run/user/…/gvfs/smb-share:server=…"""
    if not uri:
        return ""
    m = re.search(r"/gvfs/[a-z-]+:(?:[^/]*,)?(?:server|host)=([^,/]+)", urllib.parse.unquote(uri))
    if m:
        return m.group(1).lower()
    if "://" in uri and not uri.startswith("file:"):
        return (urllib.parse.urlsplit(uri).hostname or "").lower()
    return ""


def rule_for(uri, prefs):
    host = host_of(uri)
    if not host:
        return None
    for r in prefs.get("vpn") or []:
        h = (r.get("host") or "").lower().lstrip(".")
        if h and (host == h or host.endswith("." + h)) and r.get("nm"):
            return r
    return None


def _nmcli(*args, timeout=6):
    try:
        return subprocess.run(["nmcli", "-t", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_up(name):
    r = _nmcli("-f", "NAME", "connection", "show", "--active")
    return bool(r and name in r.stdout.splitlines())


def connections():
    """VPN-Verbindungen im NetworkManager → [Name] (OpenVPN, WireGuard, …)."""
    r = _nmcli("-f", "NAME,TYPE", "connection", "show")
    if not r:
        return []
    out = []
    for line in r.stdout.splitlines():
        name, _, kind = line.rpartition(":")
        if kind in ("vpn", "wireguard"):
            out.append(name.replace("\\:", ":"))
    return out


def default_cmd(name):
    """eduVPN: über den eduVPN-Client verbinden (holt bei Bedarf ein neues Zertifikat)."""
    if not name.lower().startswith("eduvpn") or not shutil.which("eduvpn-cli"):
        return None
    try:
        out = subprocess.run(["eduvpn-cli", "list"], capture_output=True, text=True, timeout=15).stdout
        n = (re.search(r"^\[(\d+)\]", out, re.M) or [None, "1"])[1]
    except (OSError, subprocess.TimeoutExpired):
        n = "1"
    return ["eduvpn-cli", "-y", "connect", "-n", n]


def set_rule(prefs, uri, name):
    """Ort → VPN merken (name None/leer = Regel entfernen). Gibt die Regel zurück."""
    host = host_of(uri)
    rules = [r for r in (prefs.get("vpn") or []) if (r.get("host") or "").lower() != host]
    rule = None
    if name:
        rule = {"host": host, "nm": name}
        cmd = default_cmd(name)
        if cmd:
            rule["cmd"] = cmd
        rules.append(rule)
    prefs["vpn"] = rules
    return rule


_busy = {}       # nm-Name → [Rückrufe], damit zwei Fenster nicht doppelt verbinden


def ensure(rule, done, step=None):
    """VPN sicherstellen (im Hintergrund). done(fehler|None, neu_verbunden) im GTK-Thread, step(text) für die Anzeige.
    neu_verbunden=True: das VPN war weg – alte Einhängungen über das VPN sind dann meist tot und gehören neu verbunden."""
    name = rule["nm"]
    if name in _busy:
        _busy[name].append(done)
        return
    _busy[name] = [done]

    def say(text):
        if step:
            GLib.idle_add(lambda: (step(text), False)[1])

    def work():
        err, fresh = None, False
        try:
            if not is_up(name):
                fresh = True
                cmd = rule.get("cmd")
                # erst direkt über den NetworkManager (Sekunden, solange das Zertifikat gilt) …
                say(f"vpn „{name}“ wird verbunden …")
                r = _nmcli("connection", "up", "id", name, timeout=45)
                if r is None or (r.returncode and not is_up(name)):
                    err = _last_line(r.stderr if r else "") or "nmcli fehlt oder antwortet nicht"
                    # … sonst der VPN-Client (eduVPN: holt ein neues Zertifikat, evtl. Anmeldung im Browser)
                    if cmd and shutil.which(cmd[0]):
                        say(f"vpn „{name}“: zertifikat wird erneuert … (evtl. im browser anmelden)")
                        err = _run_client(cmd, name)
                if not err:
                    time.sleep(0.6)          # Routen/DNS kurz setzen lassen
        except subprocess.TimeoutExpired:
            err = "vpn: zeitüberschreitung"
        except Exception as e:  # noqa: BLE001 – jeder Fehler soll im Fenster landen
            err = str(e)
        cbs = _busy.pop(name, [])
        GLib.idle_add(lambda: ([cb(err, fresh) for cb in cbs], False)[1])
    threading.Thread(target=work, daemon=True).start()


def _run_client(cmd, name, seconds=180):
    """VPN-Client starten und warten, bis die Verbindung steht. eduvpn-cli beendet sich nicht immer von selbst –
    steht das VPN, wird er nach kurzer Zeit beendet (die Verbindung hält der NetworkManager). → Fehlertext|None"""
    import tempfile
    log = tempfile.TemporaryFile(mode="w+")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            if is_up(name):
                try:
                    p.wait(8)
                except subprocess.TimeoutExpired:
                    p.terminate()
                return None
            if p.poll() is not None and not _wait_up(name, 3):
                log.seek(0)
                return _last_line(log.read()) or "vpn kam nicht hoch"
            time.sleep(0.7)
        p.terminate()
        return "vpn: zeitüberschreitung"
    finally:
        log.close()


def _wait_up(name, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if is_up(name):
            return True
        time.sleep(0.5)
    return False


def _last_line(text):
    lines = [l for l in (text or "").splitlines() if l.strip() and "level=" not in l]
    return lines[-1].strip()[:160] if lines else ""
