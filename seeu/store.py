import json
import os
import shutil
import threading
import time
from pathlib import Path

HOME = Path(os.environ.get("SEEU_HOME") or Path.home() / ".seeu")
_lock = threading.RLock()


def path(*parts):
    p = HOME.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(name, default=None):
    try:
        return json.loads(path(name).read_text())
    except (FileNotFoundError, ValueError):
        return default


def save(name, data):
    p = path(name)
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(p)


def key(platform, handle):
    return f"{platform}:{handle.lower()}"


def accounts():
    return load("accounts.json", [])


def is_added(platform, handle):
    return any(key(a["platform"], a["handle"]) == key(platform, handle) for a in accounts())


def add(platform, handle, name=None):
    with _lock:
        items = [a for a in accounts() if key(a["platform"], a["handle"]) != key(platform, handle)]
        items.append({"platform": platform, "handle": handle.lower(), "name": name})
        save("accounts.json", items)


def remove(platform, handle):
    with _lock:
        save("accounts.json", [a for a in accounts() if key(a["platform"], a["handle"]) != key(platform, handle)])


def seen(platform, handle):
    return set(load("seen.json", {}).get(key(platform, handle), []))


def mark_seen(platform, handle, ids):
    with _lock:
        s = load("seen.json", {})
        have = s.get(key(platform, handle), [])
        old = set(have)
        fresh = [i for i in ids if i not in old]
        if fresh:
            s[key(platform, handle)] = (have + fresh)[-500:]
            save("seen.json", s)


def unseen(platform, handle, posts):
    if not is_added(platform, handle):
        return set()
    s = seen(platform, handle)
    return {p["id"] for p in posts if p["id"] not in s}


def clear_platform(platform):
    shutil.rmtree(HOME / "cache" / platform, ignore_errors=True)
    for folder in ("fixes", "details", "thumbs"):
        for f in (HOME / "cache" / folder).glob(f"{platform}_*"):
            f.unlink(missing_ok=True)


def prune():
    (HOME / "known.json").unlink(missing_ok=True)
    keep = {key(a["platform"], a["handle"]) for a in accounts()}
    now = time.time()
    for platform in ("tiktok", "instagram", "snapchat"):
        for f in (HOME / "cache" / platform).glob("*.json"):
            if key(platform, f.stem) not in keep:
                f.unlink(missing_ok=True)
    for f in (HOME / "cache" / "fixes").glob("*.json"):
        platform, _, handle = f.stem.partition("_")
        if key(platform, handle) not in keep:
            f.unlink(missing_ok=True)
    for folder, age in (("details", 6 * 3600), ("thumbs", 86400)):
        for f in (HOME / "cache" / folder).glob("*"):
            if now - f.stat().st_mtime > age:
                f.unlink(missing_ok=True)
