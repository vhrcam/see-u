import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import websocket

from . import sessions

LOGIN = {
    "tiktok": {"url": "https://www.tiktok.com/login", "domain": "tiktok.com", "cookie": "sessionid"},
    "instagram": {"url": "https://www.instagram.com/accounts/login/", "domain": "instagram.com", "cookie": "sessionid"},
    "snapchat": {"url": "https://accounts.snapchat.com/accounts/v2/login?continue=https://www.snapchat.com/web",
                 "domain": "snapchat.com", "cookie": "__Host-sc-a-auth-session"},
}
IG_APP = "936619743392459"
WAIT = 900


class NoBrowser(Exception):
    pass


class Cancelled(Exception):
    pass


class TimedOut(Exception):
    pass


class Unsure(Exception):
    pass


def browser():
    if sys.platform == "darwin":
        apps = ["Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium", "Vivaldi"]
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for app in apps:
                exe = root / f"{app}.app" / "Contents" / "MacOS" / app
                if exe.exists():
                    return str(exe)
        return None
    if sys.platform == "win32":
        tails = [r"Google\Chrome\Application\chrome.exe", r"BraveSoftware\Brave-Browser\Application\brave.exe",
                 r"Microsoft\Edge\Application\msedge.exe"]
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            for tail in tails:
                exe = Path(os.environ.get(base, "")) / tail
                if exe.exists():
                    return str(exe)
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
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
    def __init__(self, profile, url):
        exe = browser()
        if not exe:
            raise NoBrowser("see u signs in through Chrome, Brave, Edge, or Chromium. install one and try again")
        Path(profile).mkdir(parents=True, exist_ok=True)
        self.ports = Path(profile) / "DevToolsActivePort"
        self.ports.unlink(missing_ok=True)
        args = [exe, f"--user-data-dir={profile}", "--remote-debugging-port=0", "--no-first-run",
                "--no-default-browser-check", "--new-window"]
        if sys.platform == "darwin":
            args.append("--use-mock-keychain")
        self.proc = subprocess.Popen(args + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        self.n = 0

    def attach(self, cancelled):
        for _ in range(150):
            if cancelled() or self.proc.poll() is not None:
                raise Cancelled()
            if self.ports.exists():
                lines = self.ports.read_text().split()
                if len(lines) == 2:
                    self.ws = websocket.create_connection(f"ws://127.0.0.1:{lines[0]}{lines[1]}", suppress_origin=True,
                                                          timeout=10)
                    return
            time.sleep(0.2)
        raise TimedOut()

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
        return self.call("Browser.getVersion").get("userAgent")

    def close(self):
        try:
            if self.ws:
                self.call("Browser.close")
                self.ws.close()
        except Exception:
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


def sign_in(platform, profile, cancelled, check=None, url=None):
    info = LOGIN[platform]
    check = check or whoami
    b = Browser(profile, url or info["url"])
    tried = None
    try:
        b.attach(cancelled)
        agent = b.agent()
        end = time.time() + WAIT
        while time.time() < end:
            if cancelled() or b.proc.poll() is not None:
                raise Cancelled()
            try:
                cookies = b.cookies(info["domain"])
            except (websocket.WebSocketException, OSError):
                raise Cancelled()
            token = next((c["value"] for c in cookies if c["name"] == info["cookie"]), None)
            if token and token != tried:
                time.sleep(1.5)
                cookies = b.cookies(info["domain"])
                try:
                    who = check(platform, cookies, agent)
                except (requests.RequestException, Unsure):
                    who = None
                    time.sleep(5)
                else:
                    tried = token
                if who:
                    return who, cookies, agent
            time.sleep(1)
        raise TimedOut()
    finally:
        b.close()


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
    jar = {c["name"]: c["value"] for c in cookies}
    uid = jar.get("ds_user_id")
    if not uid:
        return None
    s = http(cookies, agent)
    s.headers.update({"X-IG-App-ID": IG_APP, "X-CSRFToken": jar.get("csrftoken", ""),
                      "Referer": "https://www.instagram.com/"})
    r = s.get(f"https://www.instagram.com/api/v1/users/{uid}/info/", timeout=20, allow_redirects=False)
    if r.status_code in (401, 403) or r.is_redirect:
        return None
    if r.status_code != 200:
        raise Unsure()
    u = r.json().get("user") or {}
    if not u.get("username"):
        raise Unsure()
    return {"user": u["username"], "name": u.get("full_name")}


def _tiktok_me(cookies, agent):
    html = http(cookies, agent).get("https://www.tiktok.com/", timeout=20).text
    m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise Unsure()
    u = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.app-context", {}).get("user") or {}
    return {"user": u["uniqueId"], "name": u.get("nickName")} if u.get("uniqueId") else None


def _snapchat_me(cookies, agent):
    r = http(cookies, agent).get("https://accounts.snapchat.com/accounts/v2/welcome", timeout=20)
    if "/login" in r.url:
        return None
    m = re.search(r'"(?:username|userName)"\s*:\s*"([A-Za-z0-9_.-]+)"', r.text)
    if not m:
        raise Unsure()
    return {"user": m.group(1), "name": None}
