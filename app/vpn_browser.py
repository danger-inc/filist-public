# Browser für die VPN-Anmeldung (eduVPN öffnet beim Zertifikat-Erneuern eine Anmeldeseite): statt irgendwo
# in einem anderen Workspace erscheint sie als eigenes, schwebendes Fenster auf dem Workspace, der beim
# Aufruf aktiv ist (= der von filist). Aufruf: vpn_browser.py URL – wird über $BROWSER von vpn.py gesetzt.
import json, shutil, subprocess, sys, time


def _hl(*args):
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _zen():
    try:
        return {c["address"] for c in json.loads(_hl("clients", "-j")) if c.get("class", "").lower() == "zen"}
    except ValueError:
        return set()


def main(url):
    browser = shutil.which("zen-browser") or shutil.which("zen")
    if not browser or not shutil.which("hyprctl"):
        subprocess.Popen(["xdg-open", url], start_new_session=True)
        return
    try:
        ws = json.loads(_hl("activeworkspace", "-j"))["id"]
    except (ValueError, KeyError):
        ws = None
    before = _zen()
    subprocess.Popen([browser, "--new-window", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    end = time.monotonic() + 15
    while ws is not None and time.monotonic() < end:
        new = _zen() - before
        if new:
            w = f"window = 'address:{sorted(new)[0]}'"
            _hl("dispatch", f"hl.dsp.window.move({{ workspace = {ws}, follow = false, {w} }})")
            _hl("dispatch", f"hl.dsp.window.float({{ action = 'enable', {w} }})")
            _hl("dispatch", f"hl.dsp.window.resize({{ x = 900, y = 720, exact = true, {w} }})")
            _hl("dispatch", f"hl.dsp.window.center({{ {w} }})")
            _hl("dispatch", f"hl.dsp.focus({{ {w} }})")
            return
        time.sleep(0.15)


if __name__ == "__main__":
    main(sys.argv[1])
