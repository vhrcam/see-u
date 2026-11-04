import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import requests
import websocket

from . import sessions

LOGIN = {
    "tiktok": {"url": "https://www.tiktok.com/login", "domain": "tiktok.com", "cookie": "sessionid"},
    "instagram": {"url": "https://www.instagram.com/accounts/login/", "domain": "instagram.com", "cookie": "sessionid"},
    "snapchat": {"url": "https://accounts.snapchat.com/accounts/v2/login?continue=https://www.snapchat.com/web",
                 "domain": "snapchat.com", "cookie": "__Host-sc-a-auth-session"},
}
MAC_APPS = {"com.google.chrome": "Google Chrome", "com.brave.browser": "Brave Browser",
            "com.microsoft.edgemac": "Microsoft Edge", "org.chromium.chromium": "Chromium",
            "com.vivaldi.vivaldi": "Vivaldi"}
WINDOWS_APPS = {"ChromeHTML": r"Google\Chrome\Application\chrome.exe",
                "BraveHTML": r"BraveSoftware\Brave-Browser\Application\brave.exe",
                "MSEdgeHTM": r"Microsoft\Edge\Application\msedge.exe"}
LINUX_APPS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"]
IG_VIEWER = '"PolarisViewer",[],'
WAIT = 900


class NoBrowser(Exception):
    pass


class Cancelled(Exception):
    pass


class TimedOut(Exception):
    pass


class Unsure(Exception):
    pass


class Failed(Exception):
    pass


def _mac_default():
    plist = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    try:
        with open(plist, "rb") as f:
            handlers = plistlib.load(f).get("LSHandlers", [])
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    for scheme in ("https", "http"):
        for h in handlers:
            if h.get("LSHandlerURLScheme") == scheme and h.get("LSHandlerRoleAll"):
                return h["LSHandlerRoleAll"].lower()
    return None


def _windows_default():
    try:
        import winreg

        path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, "ProgId")[0]
    except OSError:
        return None


def _linux_default():
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip().removesuffix(".desktop")


def _first(default, order):
    return ([default] if default in order else []) + list(order)


def browser():
    if sys.platform == "darwin":
        for bundle in _first(_mac_default(), MAC_APPS):
            app = MAC_APPS[bundle]
            for root in (Path("/Applications"), Path.home() / "Applications"):
                exe = root / f"{app}.app" / "Contents" / "MacOS" / app
                if exe.exists():
                    return str(exe)
        return None
    if sys.platform == "win32":
        for prog in _first(_windows_default(), WINDOWS_APPS):
            for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
                exe = Path(os.environ.get(base, "")) / WINDOWS_APPS[prog]
                if exe.exists():
                    return str(exe)
        return None
    for name in _first(_linux_default(), LINUX_APPS):
        if shutil.which(name):
            return shutil.which(name)
    return None


def browser_name():
    exe = browser()
    if not exe:
        return "your browser"
    low = exe.lower()
    for key, name in (("brave", "Brave"), ("edge", "Edge"), ("chromium", "Chromium"), ("vivaldi", "Vivaldi")):
        if key in low:
            return name
    return "Chrome"


class Browser:
    def __init__(self, profile, url, headless=False):
        exe = browser()
        if not exe:
            raise NoBrowser("see u signs in through Chrome, Brave, Edge, Chromium, or Vivaldi. install one and try again")
        Path(profile).mkdir(parents=True, exist_ok=True)
        self.ports = Path(profile) / "DevToolsActivePort"
        self.ports.unlink(missing_ok=True)
        args = [exe, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check"]
        if sys.platform == "darwin":
            args.append("--use-mock-keychain")
        args += ["--headless=new", "--remote-debugging-port=0"] if headless else ["--new-window"]
        self.proc = subprocess.Popen(args + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        self.n = 0

    def attach(self):
        for _ in range(150):
            if self.proc.poll() is not None:
                raise Failed()
            if self.ports.exists():
                lines = self.ports.read_text().split()
                if len(lines) == 2:
                    self.ws = websocket.create_connection(f"ws://127.0.0.1:{lines[0]}{lines[1]}", suppress_origin=True,
                                                          timeout=10)
                    return
            time.sleep(0.2)
        raise Failed()

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result") or {}

    def cookies(self, domain):
        items = self.call("Storage.getCookies").get("cookies") or []
        return [{k: c.get(k) for k in ("name", "value", "domain", "path", "expires", "secure")}
                for c in items if c["domain"].lstrip(".").endswith(domain)]

    def agent(self):
        return self.call("Browser.getVersion").get("userAgent", "").replace("HeadlessChrome", "Chrome")

    def close(self):
        try:
            if self.ws:
                self.call("Browser.close")
                self.ws.close()
        except Exception:
            pass
        if self.proc.poll() is None and not self.ws:
            self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)


def _chrome_now():
    return int((time.time() + 11644473600) * 1000000)


def _saved_cookies(profile, domain):
    rows = []
    for db in (Path(profile) / "Default" / "Network" / "Cookies", Path(profile) / "Default" / "Cookies"):
        if not db.exists():
            continue
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True)) as con:
                rows += con.execute("SELECT name, host_key, last_update_utc FROM cookies").fetchall()
        except sqlite3.Error:
            continue
    return [(name, when) for name, host, when in rows if host.lstrip(".").endswith(domain)]


def _login_time(profile, info):
    return max((when for name, when in _saved_cookies(profile, info["domain"]) if name == info["cookie"]), default=0)


def _read_session(platform, profile, check, fresh=False):
    b = Browser(profile, "about:blank", headless=True)
    try:
        b.attach()
        cookies, agent = b.cookies(LOGIN[platform]["domain"]), b.agent()
    finally:
        b.close()
    who = None
    for _ in range(3):
        try:
            who = check(platform, cookies, agent)
            break
        except (requests.RequestException, ValueError, Unsure):
            time.sleep(3)
    if who:
        return who, cookies, agent
    return ({"user": "account", "name": None}, cookies, agent) if fresh else None


def sign_in(platform, profile, cancelled, check=None, url=None):
    info = LOGIN[platform]
    check = check or whoami
    if _login_time(profile, info):
        found = _read_session(platform, profile, check)
        if found:
            return found
    start = _chrome_now()
    window = Browser(profile, url or info["url"])
    try:
        end = time.time() + WAIT
        while True:
            closed = window.proc.poll() is not None
            if _login_time(profile, info) > start:
                break
            if cancelled() or closed:
                raise Cancelled()
            if time.time() > end:
                raise TimedOut()
            time.sleep(1)
    finally:
        window.close()
    return _read_session(platform, profile, check, fresh=True)


def recheck():
    changed = False
    for l in sessions.logins():
        if not sessions.is_on(l):
            continue
        s = sessions.read(l)
        try:
            who = whoami(l["platform"], s["cookies"], s.get("agent"))
        except Exception:
            continue
        if who is None:
            sessions.expire(l)
            changed = True
    return changed


def http(cookies, agent):
    s = requests.Session()
    if agent:
        s.headers["User-Agent"] = agent
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path") or "/")
    return s


def whoami(platform, cookies, agent):
    return {"tiktok": _tiktok_me, "instagram": _instagram_me, "snapchat": _snapchat_me}[platform](cookies, agent)


def _instagram_me(cookies, agent):
    if not any(c["name"] == "ds_user_id" for c in cookies):
        return None
    r = http(cookies, agent).get("https://www.instagram.com/", timeout=20, allow_redirects=False)
    if r.is_redirect:
        where = urlparse(r.headers.get("location", "")).path
        if where == "/" or where.startswith("/accounts/login"):
            return None
        raise Unsure()
    start = r.text.find(IG_VIEWER)
    if r.status_code != 200 or start < 0:
        raise Unsure()
    viewer = json.JSONDecoder().raw_decode(r.text, start + len(IG_VIEWER))[0]
    if viewer.get("data") is None:
        return None
    u = viewer["data"]
    if not u.get("username"):
        raise Unsure()
    return {"user": u["username"], "name": u.get("full_name")}


def _tiktok_me(cookies, agent):
    r = http(cookies, agent).get("https://www.tiktok.com/passport/web/account/info/", params={"aid": "1459"}, timeout=20)
    if r.status_code != 200:
        raise Unsure()
    d = r.json()
    u = d.get("data") or {}
    if u.get("username"):
        return {"user": u["username"], "name": u.get("screen_name")}
    if d.get("message") == "error" and u.get("error_code"):
        return None
    raise Unsure()


def _snapchat_me(cookies, agent):
    r = http(cookies, agent).get("https://accounts.snapchat.com/accounts/v2/welcome", timeout=20)
    if "/login" in r.url:
        return None
    m = re.search(r'"(?:username|userName)"\s*:\s*"([A-Za-z0-9_.-]+)"', r.text)
    if not m:
        raise Unsure()
    return {"user": m.group(1), "name": None}
