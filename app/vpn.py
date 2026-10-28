# VPN vor Netzlaufwerken: manche Orte (z. B. das Uni-Laufwerk) sind nur über ein VPN erreichbar.
# Für solche Orte merkt sich filist eine VPN-Verbindung des NetworkManagers und baut sie beim Öffnen
# automatisch auf – erst danach wird eingehängt. Regeln stehen in prefs["vpn"] (nicht im Code):
#   [{"host": "laufwerk.uni.example", "nm": "eduVPN", "cmd": ["eduvpn-cli", "-y", "connect", "-n", "1"]}]
# „host“ passt auf den Server selbst und alles darunter (Endung); „cmd“ ist optional und läuft statt
# „nmcli con up“ – bei eduVPN nötig, weil dessen Zertifikat nach ein paar Stunden abläuft und nur der
# eduVPN-Client ein neues holt (meist ohne Rückfrage, sonst einmal im Browser anmelden).
import os, re, shutil, signal, subprocess, sys, threading, time, urllib.parse

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
_stop = {}       # nm-Name → Event: Abbrechen-Knopf (beendet nmcli/eduvpn-cli und wartet nicht weiter)


class Cancelled(Exception):
    pass


def cancel(name):
    """Laufenden Verbindungsaufbau abbrechen (der Rückruf von ensure() bekommt dann Fehler „abgebrochen“)."""
    ev = _stop.get(name)
    if ev is not None:
        ev.set()


def friendly(err):
    """Rohe Fehlerzeile von nmcli/eduvpn-cli → Satz, der sagt, was man tun kann."""
    e = (err or "").strip()
    low = e.lower()
    if not e:
        return "verbindung kam nicht zustande"
    if "zeitüberschreitung" in low or "timeout" in low or "timed out" in low:
        return "keine antwort vom vpn – internet da? dann nochmal versuchen (ggf. im eduvpn-fenster anmelden)"
    if "no secrets" in low or "secrets were required" in low or "passwor" in low:
        return "vpn braucht eine anmeldung – einmal in der netzwerk-/eduvpn-app verbinden"
    if "not found" in low or "unknown connection" in low or "no such connection" in low:
        return "vpn-verbindung existiert nicht mehr – rechtsklick auf den ort → „vpn vorher verbinden“ neu wählen"
    if "network is unreachable" in low or "no route" in low or "nicht erreichbar" in low:
        return "kein netz – ist das wlan/kabel verbunden?"
    if "fehlt oder antwortet nicht" in low:
        return "networkmanager (nmcli) fehlt oder antwortet nicht"
    return e


def _kill(p):
    """Prozess samt Kindern beenden (eigene Sitzung → ganze Gruppe)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            return
        try:
            p.wait(3)
            return
        except subprocess.TimeoutExpired:
            pass


def _run(cmd, stop, timeout):
    """subprocess.run, das auf den Abbrechen-Knopf hört. → (returncode, ausgabe) | None bei Start-/Zeitfehler"""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             start_new_session=True)
    except OSError:
        return None
    end = time.monotonic() + timeout
    while p.poll() is None:
        if stop.is_set() or time.monotonic() > end:
            _kill(p)
            if stop.is_set():
                raise Cancelled()
            raise subprocess.TimeoutExpired(cmd, timeout)
        time.sleep(0.2)
    out, err = p.communicate()
    return p.returncode, (err or out or "")


def ensure(rule, done, step=None):
    """VPN sicherstellen (im Hintergrund). done(fehler|None, neu_verbunden) im GTK-Thread, step(text) für die Anzeige.
    neu_verbunden=True: das VPN war weg – alte Einhängungen über das VPN sind dann meist tot und gehören neu verbunden."""
    name = rule["nm"]
    if name in _busy:
        _busy[name].append(done)
        return
    _busy[name] = [done]
    stop = _stop[name] = threading.Event()

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
                r = _run(["nmcli", "-t", "connection", "up", "id", name], stop, 45)
                if r is None or (r[0] and not is_up(name)):
                    err = _last_line(r[1] if r else "") or "nmcli fehlt oder antwortet nicht"
                    # … sonst der VPN-Client (eduVPN: holt ein neues Zertifikat, evtl. Anmeldung im Browser)
                    if cmd and shutil.which(cmd[0]):
                        say(f"vpn „{name}“: zertifikat wird erneuert … (evtl. im browser anmelden)")
                        err = _run_client(cmd, name, stop=stop)
                if not err:
                    time.sleep(0.6)          # Routen/DNS kurz setzen lassen
        except Cancelled:
            err = "abgebrochen"
        except subprocess.TimeoutExpired:
            err = "zeitüberschreitung"
        except Exception as e:  # noqa: BLE001 – jeder Fehler soll im Fenster landen
            err = str(e)
        if err and err != "abgebrochen":
            err = friendly(err)
        _stop.pop(name, None)
        cbs = _busy.pop(name, [])
        GLib.idle_add(lambda: ([cb(err, fresh) for cb in cbs], False)[1])
    threading.Thread(target=work, daemon=True).start()


def _run_client(cmd, name, seconds=180, stop=None):
    """VPN-Client starten und warten, bis die Verbindung steht. eduvpn-cli beendet sich nicht immer von selbst –
    steht das VPN, wird er nach kurzer Zeit beendet (die Verbindung hält der NetworkManager). → Fehlertext|None"""
    import tempfile
    log = tempfile.TemporaryFile(mode="w+")
    # Anmeldeseite als schwebendes Fenster auf dem aktuellen (filist-)Workspace statt im Browser irgendwo anders
    env = dict(os.environ, BROWSER=f"{sys.executable} {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vpn_browser.py')} %s")
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=env)
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            if stop is not None and stop.is_set():
                _kill(p)
                return "abgebrochen"
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
        _kill(p)
        return "zeitüberschreitung"
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
