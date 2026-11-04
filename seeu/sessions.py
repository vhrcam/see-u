import json
import os
import shutil

from . import store

PLATFORMS = ["tiktok", "instagram", "snapchat"]
UNKNOWN = "account"


class SignedOut(Exception):
    pass


def _private(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    return p


def _name(platform, user):
    return f"{platform}_{user.lower()}"


def session_file(platform, user):
    return _private(store.HOME / "sessions" / f"{_name(platform, user)}.json")


def browser_profile(platform, user):
    return _private(store.HOME / "browser" / _name(platform, user))


def logins():
    return store.load("logins.json", [])


def is_on(login):
    return session_file(login["platform"], login["user"]).exists()


def current(platform):
    on = [l for l in logins() if l["platform"] == platform and is_on(l)]
    return next((l for l in on if l.get("using")), on[0] if on else None)


def connected():
    return [p for p in PLATFORMS if current(p)]


def require(platform):
    if not current(platform):
        raise SignedOut(f"connect {platform} first: press l on the home screen")


def read(login):
    try:
        return json.loads(session_file(login["platform"], login["user"]).read_text())
    except (OSError, ValueError):
        return {"cookies": [], "agent": None}


def session(platform):
    login = current(platform)
    return read(login) if login else {"cookies": [], "agent": None}


def jar(platform):
    return {c["name"]: c["value"] for c in session(platform)["cookies"]}


def _write(login_list):
    store.save("logins.json", login_list)


def add(platform, user, name, cookies, agent):
    user = user.lower()
    with store._lock:
        rows = [l for l in logins() if not (l["platform"] == platform and l["user"] == user)]
        for l in rows:
            if l["platform"] == platform:
                l["using"] = False
        rows.append({"platform": platform, "user": user, "name": name, "using": True})
        f = session_file(platform, user)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump({"cookies": cookies, "agent": agent}, out)
        _write(rows)


def rename(login, user, name):
    platform, old = login["platform"], login["user"]
    user = user.lower()
    with store._lock:
        rows = [l for l in logins() if not (l["platform"] == platform and l["user"] == user)]
        for l in rows:
            if l["platform"] == platform and l["user"] == old:
                l["user"], l["name"] = user, name
        session_file(platform, old).replace(session_file(platform, user))
        profile = browser_profile(platform, old)
        if profile.exists():
            shutil.rmtree(browser_profile(platform, user), ignore_errors=True)
            profile.replace(browser_profile(platform, user))
        _write(rows)


def use(login):
    with store._lock:
        rows = logins()
        for l in rows:
            if l["platform"] == login["platform"]:
                l["using"] = l["user"] == login["user"]
        _write(rows)


def expire(login):
    session_file(login["platform"], login["user"]).unlink(missing_ok=True)
    store.clear_platform(login["platform"])


def sign_out(login):
    expire(login)
    shutil.rmtree(browser_profile(login["platform"], login["user"]), ignore_errors=True)


def forget(login):
    sign_out(login)
    with store._lock:
        _write([l for l in logins() if not (l["platform"] == login["platform"] and l["user"] == login["user"])])
