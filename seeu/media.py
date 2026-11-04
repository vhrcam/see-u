import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image, ImageOps
from rich.text import Text

from . import fetch, store

VIEW_LIMIT = 20
SAVE_LIMIT = 50
KEYS = """s ignore
S ignore
w ignore
W ignore
ENTER ignore
KP_ENTER ignore
MBTN_LEFT playlist-next
MBTN_LEFT_DBL ignore
SPACE cycle pause
ESC quit
q quit
"""
RAMP = [(0.0, (0, 0, 0)), (0.3, (8, 51, 68)), (0.55, (14, 116, 144)), (0.8, (34, 211, 238)), (1.0, (236, 254, 255))]

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}


def get(url, path, headers=None, cookies=None):
    with requests.get(url, headers=headers or UA, cookies=cookies, timeout=30, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return str(path)


def download(post, dest, media=None, limit=None):
    dest.mkdir(parents=True, exist_ok=True)
    if post["platform"] == "tiktok" and not media and not post.get("media"):
        try:
            media = fetch.details(post).get("media")
        except fetch.Unavailable:
            media = None
        if not media:
            with fetch.ydl(outtmpl=str(dest / "%(id)s.%(ext)s"), format="best[ext=mp4]/best") as y:
                info = y.extract_info(post["url"], download=True)
                return [y.prepare_filename(info)]
    media = (media or post.get("media") or fetch.details(post)["media"])[:limit]
    jobs = [(url, dest / f"{post['id']}_{i + 1}.{'mp4' if kind == 'video' else 'jpg'}") for i, (kind, url) in enumerate(media)]
    ask = fetch.media_request(post)
    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(lambda job: get(*job, **ask), jobs))


def player():
    found = shutil.which("mpv")
    if not found:
        found = next((p for p in ("/opt/homebrew/bin/mpv", "/usr/local/bin/mpv") if os.path.exists(p)), None)
    return found


def play(files):
    mpv = player()
    if mpv:
        keys = store.path("mpv-keys.conf")
        ahead, back = ("playlist-next", "playlist-prev") if len(files) > 1 else ("seek 3 exact", "seek -3 exact")
        keys.write_text(f"d {ahead}\nD {ahead}\na {back}\nA {back}\n" + KEYS)
        subprocess.run([mpv, "--no-config", f"--input-conf={keys}", "--force-window=immediate", "--keep-open=yes",
                        "--image-display-duration=inf", "--loop-file=inf", "--title=see u",
                        "--osd-playing-msg=${playlist-pos-1} / ${playlist-count}", "--osd-duration=1200",
                        "--autofit-larger=85%x85%", "--really-quiet", "--", *files],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    if sys.platform == "darwin":
        subprocess.run(["open", *files])
    elif sys.platform == "win32":
        os.startfile(files[0])
    else:
        subprocess.run(["xdg-open", files[0]])
    return False


def view(post, media=None, limit=VIEW_LIMIT):
    dest = store.HOME / "tmp" / f"{post['platform']}_{post['id']}"
    waited = False
    try:
        waited = play(download(post, dest, media, limit))
    finally:
        if waited:
            shutil.rmtree(dest, ignore_errors=True)


def copy(text):
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode())
            return True
    return False


def clean():
    shutil.rmtree(store.HOME / "tmp", ignore_errors=True)


def folder_name(name):
    name = re.sub(r"[^\w -]", "", name or "").strip()
    return name[:40] or "saved"


def folders():
    root = store.HOME / "saved"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def save(post, folder, extra=None):
    extra = extra or {}
    if post["platform"] == "instagram" and not extra.get("media"):
        extra = dict(extra, **fetch.details(post))
    dest = store.HOME / "saved" / folder_name(folder) / f"{post['platform']}_{post['id']}"
    files = download(post, dest, extra.get("media"), SAVE_LIMIT)
    thumb = thumb_file(post)
    if thumb:
        shutil.copy(thumb, dest / "thumb.jpg")
    meta = dict(post)
    meta.update({k: v for k, v in extra.items() if k in ("likes", "comments", "time") and v is not None})
    meta.update({"saved_at": time.time(), "files": [Path(f).name for f in files]})
    (dest / "post.json").write_text(json.dumps(meta, ensure_ascii=False))
    return dest


def saved():
    out = []
    for meta in (store.HOME / "saved").glob("*/*/post.json"):
        try:
            p = json.loads(meta.read_text())
        except ValueError:
            continue
        p["saved_dir"] = str(meta.parent)
        p["folder"] = meta.parent.parent.name
        out.append(p)
    return sorted(out, key=lambda p: -p.get("saved_at", 0))


def saved_files(p):
    d = Path(p["saved_dir"])
    return [str(d / f) for f in p.get("files", []) if (d / f).exists()]


def saved_thumb(p, cols, rows):
    d = Path(p["saved_dir"])
    pic = d / "thumb.jpg"
    if not pic.exists():
        pic = next((d / f for f in p.get("files", []) if f.endswith(".jpg")), None)
    return render(pic, cols, rows) if pic and pic.exists() else None


def thumb_file(post):
    path = store.path("cache", "thumbs", f"{post['platform']}_{post['id']}.jpg")
    if not path.exists() and post.get("thumb"):
        get(post["thumb"], path)
    return path if path.exists() else None


def cyan(v):
    for (a, ca), (b, cb) in zip(RAMP, RAMP[1:]):
        if v <= b:
            t = (v - a) / (b - a)
            return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(ca, cb))
    return "#ecfeff"


def render(path, cols, rows):
    img = Image.open(path).convert("L")
    w, h = img.size
    scale = min(cols / w, rows * 2 / h)
    pw, ph = max(1, int(w * scale)), max(2, int(h * scale) // 2 * 2)
    img = ImageOps.autocontrast(img.resize((pw, ph), Image.LANCZOS), cutoff=1)
    px = img.load()
    t = Text(no_wrap=True)
    for y in range(0, ph, 2):
        for x in range(pw):
            t.append("▀", style=f"{cyan(px[x, y] / 255)} on {cyan(px[x, y + 1] / 255)}")
        if y + 2 < ph:
            t.append("\n")
    return t


def thumbnail(post, cols=40, rows=15):
    path = thumb_file(post)
    return render(path, cols, rows) if path else None
