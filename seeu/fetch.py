import http.cookiejar
import json
import re
import threading
import time
from datetime import datetime
from datetime import time as clock
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import instaloader
import requests
import yt_dlp

from . import sessions, store

TTL = 600
IG_EPOCH = 1314220021721
PACIFIC = ZoneInfo("America/Los_Angeles")
POSTED = re.compile(r" on ([A-Z][a-z]+ \d{1,2}, \d{4})")
IG_POSTS_DOC = "28904005159232689"
IG_PAUSE = 3
IG_PAGE = {"Accept": "text/html", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"}
LINKS = [
    ("tiktok", re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)", re.I)),
    ("instagram", re.compile(r"instagram\.com/(?!p/|reels?/|stories/|explore/)([A-Za-z0-9_.]+)", re.I)),
    ("snapchat", re.compile(r"snapchat\.com/(?:add/|@)([A-Za-z0-9_.-]+)", re.I)),
]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9"}
_locks = {"tiktok": threading.Lock(), "instagram": threading.Lock()}
_iters = {}


class NotFound(Exception):
    pass


class Unavailable(Exception):
    pass


class NeedsLogin(Unavailable):
    pass


def need(platform):
    try:
        sessions.require(platform)
    except sessions.SignedOut as e:
        raise NeedsLogin(str(e))
    if platform == "instagram":
        _ig_session()


def _agent(platform):
    return sessions.session(platform).get("agent") or UA["User-Agent"]


_ig_user = None


def _ig_session():
    global _ig_user
    login = sessions.current("instagram")
    jar = sessions.jar("instagram")
    if "csrftoken" not in jar:
        raise NeedsLogin("this instagram session is incomplete. reconnect it with l on the home screen")
    if (login["user"], jar.get("sessionid")) != _ig_user:
        _loader.context.user_agent = _agent("instagram")
        _loader.load_session(login["user"], jar)
        _ig_user = (login["user"], jar.get("sessionid"))


class _Quick(instaloader.RateController):
    def sleep(self, secs):
        time.sleep(min(secs, 2))


_loader = instaloader.Instaloader(
    quiet=True,
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    save_metadata=False,
    max_connection_attempts=1,
    rate_controller=lambda ctx: _Quick(ctx),
)


def parse(text):
    text = text.strip()
    for platform, rx in LINKS:
        m = rx.search(text)
        if m:
            return platform, m.group(1).lower()
    if "://" in text or ".com/" in text:
        return None, ""
    words = text.lstrip("@:").split()
    return None, words[0].lstrip("@").lower() if words else ""


def ydl(**opts):
    y = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noprogress": True, "geo_bypass": False, **opts})
    s = sessions.session("tiktok")
    if s.get("agent"):
        y.params["http_headers"]["User-Agent"] = s["agent"]
    for c in s["cookies"]:
        y.cookiejar.set_cookie(http.cookiejar.Cookie(
            0, c["name"], c["value"], None, False, c["domain"], True, c["domain"].startswith("."),
            c.get("path") or "/", True, bool(c.get("secure")), max(int(c.get("expires") or 0), 0) or None, False, None, None, {}))
    return y


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _tiktok_page(y, handle):
    html = y.urlopen(f"https://www.tiktok.com/@{handle}").read().decode("utf-8", "replace")
    m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    detail = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    info = detail.get("userInfo") or {}
    u = info.get("user") or {}
    if not u.get("uniqueId"):
        return {"missing": True}
    s = info.get("statsV2") or info.get("stats") or {}
    return {
        "handle": u["uniqueId"].lower(),
        "name": u.get("nickname") or u["uniqueId"],
        "bio": u.get("signature") or "",
        "followers": _num(s.get("followerCount")),
        "following": _num(s.get("followingCount")),
        "likes": _num(s.get("heartCount")),
        "posts": _num(s.get("videoCount")),
        "verified": bool(u.get("verified")),
        "private": bool(u.get("privateAccount")),
        "sec_uid": u.get("secUid"),
    }


def _tt_post(e, handle):
    thumbs = e.get("thumbnails") or []
    cover = next((t["url"] for t in thumbs if t.get("id") == "cover"), thumbs[0]["url"] if thumbs else None)
    vid = str(e["id"])
    return {
        "platform": "tiktok",
        "handle": handle,
        "id": vid,
        "url": f"https://www.tiktok.com/@{handle}/video/{vid}",
        "time": e.get("timestamp"),
        "text": e.get("description") or e.get("title") or "",
        "kind": "video",
        "thumb": cover,
        "views": e.get("view_count"),
        "likes": e.get("like_count"),
        "comments": e.get("comment_count"),
        "duration": e.get("duration"),
        "pinned": False,
    }


def _tiktok(handle, start=1, sec_uid=None):
    prof = None
    with ydl(extract_flat=True, playlist_items=f"{start}-{start + 11}") as y:
        if start == 1:
            try:
                prof = _tiktok_page(y, handle)
            except Exception:
                prof = None
            if prof and prof.get("missing"):
                raise NotFound(handle)
            sec_uid = (prof or {}).get("sec_uid")
        if prof and prof.get("private"):
            return prof, []
        target = f"tiktokuser:{sec_uid}" if sec_uid else f"https://www.tiktok.com/@{handle}"
        try:
            info = y.extract_info(target, download=False)
            entries = list(info.get("entries") or [])
        except yt_dlp.utils.DownloadError as e:
            msg = str(e)
            if "secondary user ID" in msg or "does not exist" in msg:
                raise NotFound(handle)
            raise Unavailable("tiktok wouldn't load that right now, try again in a bit")
    if start == 1 and not prof:
        first = entries[0] if entries else {}
        prof = {"handle": handle, "name": first.get("channel") or handle, "bio": "", "followers": None,
                "following": None, "likes": None, "posts": None, "verified": False, "private": False,
                "sec_uid": None}
    return prof, [_tt_post(e, handle) for e in entries]


def _ig_time(pk, caption):
    decoded = ((pk >> 23) + IG_EPOCH) // 1000 if pk else None
    m = POSTED.search(caption or "")
    if not m:
        return decoded
    try:
        day = datetime.strptime(m.group(1), "%B %d, %Y").date()
    except ValueError:
        return decoded
    if decoded and datetime.fromtimestamp(decoded, PACIFIC).date() == day:
        return decoded
    return int(datetime.combine(day, clock(12), PACIFIC).timestamp())


def _ig_post(n, handle):
    pk = _num(n.get("pk")) or 0
    kind = "reel" if n.get("product_type") == "clips" else {2: "video", 8: "carousel"}.get(n.get("media_type"), "photo")
    return {
        "platform": "instagram",
        "handle": handle,
        "id": n["code"],
        "url": f"https://www.instagram.com/p/{n['code']}/",
        "time": _ig_time(pk, n.get("accessibility_caption")),
        "approx": True,
        "text": (n.get("caption") or {}).get("text") or "",
        "kind": kind,
        "thumb": n.get("display_uri"),
        "views": None,
        "likes": None,
        "comments": None,
        "duration": None,
        "pinned": False,
    }


def _ig_errors(fn):
    try:
        return fn()
    except instaloader.QueryReturnedNotFoundException:
        raise NotFound("")
    except instaloader.TooManyRequestsException:
        raise Unavailable("instagram is rate-limiting right now, try again in a few minutes")
    except instaloader.LoginRequiredException:
        raise Unavailable("instagram says this session has ended. reconnect it with l on the home screen")
    except instaloader.AbortDownloadException:
        raise Unavailable("instagram stopped that request. give it a while and check your account in a browser")
    except instaloader.ConnectionException:
        raise Unavailable("instagram wouldn't load that right now, try again in a bit")


def _embedded(obj):
    if isinstance(obj, dict):
        box = obj.get("__bbox")
        result = box.get("result") if isinstance(box, dict) else None
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            yield result["data"]
        for v in obj.values():
            yield from _embedded(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _embedded(v)


def _ig_page(path):
    try:
        r = requests.get(f"https://www.instagram.com/{path}", cookies=sessions.jar("instagram"), timeout=25,
                         allow_redirects=False, headers={**IG_PAGE, "User-Agent": _agent("instagram")})
    except requests.RequestException:
        raise Unavailable("instagram wouldn't load that right now, try again in a bit")
    if r.status_code == 404:
        raise NotFound(path)
    if r.status_code == 429:
        raise Unavailable("instagram is rate-limiting right now, try again in a few minutes")
    if r.is_redirect:
        where = urlparse(r.headers.get("location", "")).path
        if where == "/" or where.startswith("/accounts/login"):
            raise NeedsLogin("instagram says this session has ended. reconnect it with l on the home screen")
        raise Unavailable("instagram wants to check your account. open it in a browser, then try again")
    if r.status_code != 200:
        raise Unavailable("instagram wouldn't load that right now, try again in a bit")
    parts = []
    for script in re.finditer(r'<script type="application/json"[^>]*>(.*?)</script>', r.text, re.S):
        try:
            parts.extend(_embedded(json.loads(script.group(1))))
        except ValueError:
            continue
    return parts


def _instagram(handle):
    with _locks["instagram"]:
        try:
            page = _ig_errors(lambda: _loader.context.get_page_data(f"{handle}/"))
        except NotFound:
            raise NotFound(handle)
    user, timeline = {}, None
    for d in page:
        x = d.get("xig_user_by_username") or {}
        user.update(x)
        if "polaris_ordered_timeline_connection" in x:
            timeline = x
    if not user.get("pk"):
        raise NotFound(handle)
    handle = (user.get("username") or handle).lower()
    links = [b.get("url") for b in user.get("bio_links") or [] if b.get("url")]
    try:
        highlights = [e["node"].get("title") for e in user["lox_highlights_connection"]["edges"]]
    except (KeyError, TypeError):
        highlights = []
    conn = (timeline or {}).get("polaris_ordered_timeline_connection") or {}
    info = conn.get("page_info") or {}
    prof = {
        "handle": handle,
        "name": user.get("full_name") or handle,
        "bio": user.get("biography") or "",
        "followers": user.get("follower_count"),
        "following": user.get("following_count"),
        "likes": None,
        "posts": user.get("all_media_count") or user.get("media_count"),
        "verified": bool(user.get("is_verified")),
        "private": bool(user.get("is_private")),
        "links": links,
        "highlights": [h for h in highlights if h],
        "page_id": (timeline or {}).get("id"),
        "cursor": info.get("end_cursor") if info.get("has_next_page") else None,
    }
    return prof, [_ig_post(e["node"], handle) for e in conn.get("edges") or []]


def _v(x):
    return x.get("value") if isinstance(x, dict) else x


def _snaps(snaps):
    media = []
    for s in snaps:
        url = (s.get("snapUrls") or {}).get("mediaUrl")
        if url:
            media.append(["video" if s.get("snapMediaType") == 1 else "photo", url])
    return media


def _snap_time(snaps):
    return max((_num(_v(s.get("timestampInSec"))) or 0 for s in snaps), default=0) or None


def _snap_post(handle, pid, kind, text, thumb, when, media, url, views=None, first=None):
    return {"platform": "snapchat", "handle": handle, "id": pid, "url": url, "time": when, "text": text,
            "kind": kind, "thumb": thumb, "views": views, "likes": None, "comments": None, "duration": None,
            "pinned": False, "media": media, "items": len(media) if kind != "spotlight" else None, "first": first}


def _snap_first(snaps):
    return min((_num(_v(s.get("timestampInSec"))) or 0 for s in snaps), default=0) or None


def _snapchat(handle):
    try:
        r = requests.get(f"https://www.snapchat.com/add/{handle}", timeout=20,
                         headers={**UA, "User-Agent": _agent("snapchat")}, cookies=sessions.jar("snapchat"))
    except requests.RequestException:
        raise Unavailable("snapchat wouldn't load that right now, try again in a bit")
    if r.status_code == 404:
        raise NotFound(handle)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if r.status_code != 200 or not m:
        raise Unavailable("snapchat wouldn't load that right now, try again in a bit")
    page = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    up = page.get("userProfile") or {}
    info = up.get(up.get("$case") or "") or {}
    if not info.get("username"):
        raise NotFound(handle)
    handle = info["username"].lower()
    home = f"https://www.snapchat.com/add/{handle}"
    prof = {
        "handle": handle,
        "name": info.get("title") or info.get("displayName") or handle,
        "bio": info.get("bio") or "",
        "followers": _num(info.get("subscriberCount")),
        "following": None,
        "likes": None,
        "posts": None,
        "verified": bool(info.get("badge")),
        "private": up.get("$case") != "publicProfileInfo",
        "links": [info["websiteUrl"]] if info.get("websiteUrl") else [],
        "highlights": [],
    }
    posts = []
    story = page.get("story") or {}
    snaps = story.get("snapList") or []
    if snaps:
        when = _snap_time(snaps)
        thumb = (snaps[-1].get("snapUrls") or {}).get("mediaPreviewUrl") or _v(story.get("thumbnailUrl"))
        label = "live story" if info.get("hasStory") else "recent story"
        posts.append(_snap_post(handle, f"story-{when}", "story", label, thumb, when, _snaps(snaps), home,
                                first=_snap_first(snaps)))
    for i, h in enumerate(page.get("curatedHighlights") or []):
        snaps = h.get("snapList") or []
        media = _snaps(snaps)
        if not media:
            continue
        first = min((_num(_v(s.get("timestampInSec"))) or 0 for s in snaps), default=0)
        suffix = _v(h.get("canonicalUrlSuffix"))
        hid = f"hl-{first}-{_snap_time(snaps)}-{len(media)}" if first else f"hl-{i}"
        posts.append(_snap_post(handle, hid, "highlight", _v(h.get("storyTitle")) or "saved story",
                                _v(h.get("thumbnailUrl")), _snap_time(snaps), media,
                                f"https://www.snapchat.com{suffix}" if suffix else home, first=first or None))
    for i, s in enumerate(page.get("spotlightStoryMetadata") or []):
        vm = s.get("videoMetadata") or {}
        if not vm.get("contentUrl"):
            continue
        when = (_num(vm.get("uploadDateMs")) or 0) // 1000 or None
        posts.append(_snap_post(handle, f"spot-{when or i}", "spotlight",
                                vm.get("description") or vm.get("name") or "spotlight", vm.get("thumbnailUrl"), when,
                                [["video", vm["contentUrl"]]], s.get("deeplink") or home, _num(vm.get("viewCount"))))
    taken = set()
    for p in posts:
        while p["id"] in taken:
            p["id"] += "x"
        taken.add(p["id"])
    return prof, posts


def _mark_pinned(posts):
    times = [p["time"] or 0 for p in posts]
    if not times:
        return
    top = times.index(max(times))
    for p in posts[:top]:
        p["pinned"] = True


def tiktok_name(handle):
    try:
        r = requests.get("https://www.tiktok.com/oembed", params={"url": f"https://www.tiktok.com/@{handle}"}, timeout=10)
        name = r.json().get("author_name") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None
    return name


def cache_name(platform, handle):
    return f"cache/{platform}/{handle.lower()}.json"


def fixes_name(platform, handle):
    return f"cache/fixes/{platform}_{handle.lower()}.json"


def fix(post, **fields):
    with store._lock:
        name = fixes_name(post["platform"], post["handle"])
        fixes = store.load(name, {})
        fixes.setdefault(post["id"], {}).update(fields)
        store.save(name, fixes)
    post.update(fields)


def _overlay(platform, handle, c):
    fixes = store.load(fixes_name(platform, handle), {})
    for p in c["posts"]:
        if p["id"] in fixes:
            p.update(fixes[p["id"]])
    return c


def cached(platform, handle):
    c = store.load(cache_name(platform, handle))
    prof = (c or {}).get("profile") or {}
    if prof.get("followers") is None and prof.get("subscribers") is not None:
        prof["followers"] = prof.pop("subscribers")
    return _overlay(platform, handle, c) if c else c


def _order(posts):
    return sorted(posts, key=lambda p: (not p.get("pinned"), -(p["time"] or 0)))


_missing = {}


def account(platform, handle, fresh=False):
    need(platform)
    handle = handle.lower()
    c = cached(platform, handle)
    if c and not fresh and time.time() - c["time"] < TTL:
        return c
    if not fresh and time.time() - _missing.get((platform, handle), 0) < TTL:
        raise NotFound(handle)
    try:
        prof, posts = {"tiktok": _tiktok, "instagram": _instagram, "snapchat": _snapchat}[platform](handle)
    except NotFound:
        _missing[(platform, handle)] = time.time()
        raise
    if platform != "snapchat":
        _mark_pinned(posts)
    c = {"time": time.time(), "profile": prof, "posts": _order(posts), "end": platform == "snapchat", "cursor": None}
    old = cached(platform, prof["handle"])
    if old and platform != "snapchat":
        ids = {p["id"] for p in posts}
        c["posts"] = _order(posts + [dict(p, pinned=False) for p in old["posts"] if p["id"] not in ids])
        c["end"], c["cursor"] = old.get("end", False), old.get("cursor")
    store.save(cache_name(platform, prof["handle"]), c)
    return _overlay(platform, prof["handle"], c)


def older(platform, handle, step, stop):
    need(platform)
    handle = handle.lower()
    c = cached(platform, handle) or account(platform, handle)
    if platform == "snapchat":
        c["end"] = True
        return c
    have = {p["id"] for p in c["posts"]}

    def add(posts):
        fresh = [p for p in posts if p["id"] not in have]
        have.update(p["id"] for p in fresh)
        c["posts"] += fresh
        store.save(cache_name(platform, handle), c)
        step(c, fresh)

    if platform == "tiktok":
        sec = c["profile"].get("sec_uid")
        target = f"tiktokuser:{sec}" if sec else f"https://www.tiktok.com/@{handle}"
        batch = []
        stopped = False
        try:
            with ydl(extract_flat=True, lazy_playlist=True) as y:
                for e in y.extract_info(target, download=False).get("entries") or []:
                    if stop():
                        stopped = True
                        break
                    if str(e.get("id")) in have:
                        continue
                    batch.append(_tt_post(e, handle))
                    if len(batch) >= 30:
                        add(batch)
                        batch = []
        except yt_dlp.utils.DownloadError:
            raise Unavailable("tiktok stopped sending older posts for now")
        finally:
            if batch:
                add(batch)
        if not stopped:
            total = c["profile"].get("posts") or 0
            c["end"] = len(c["posts"]) >= total
            c["short"] = not c["end"]
    else:
        it = _iters.get(handle)
        if it is None:
            cursor = c.get("cursor") or c["profile"].get("cursor")
            if not cursor:
                c["end"] = True
                store.save(cache_name(platform, handle), c)
                return c
            it = instaloader.NodeIterator(
                context=_loader.context,
                query_hash=None,
                doc_id=IG_POSTS_DOC,
                web_api=True,
                edge_extractor=lambda d: d["data"]["node"]["polaris_ordered_timeline_connection"],
                node_wrapper=lambda n: n,
                query_variables={"id": c["profile"]["page_id"],
                                 "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False},
                query_referer=f"https://www.instagram.com/{handle}/",
                first_data={"edges": [], "page_info": {"has_next_page": True, "end_cursor": cursor}},
            )
            _iters[handle] = it
        while not stop():
            nodes = []

            def take():
                for n in it:
                    nodes.append(n)
                    if len(nodes) >= 12:
                        break

            with _locks["instagram"]:
                _ig_errors(take)
            page = (getattr(it, "_data", None) or {}).get("page_info") or {}
            c["cursor"] = page.get("end_cursor") if page.get("has_next_page") else None
            if not nodes:
                c["end"] = True
                break
            add([_ig_post(n, handle) for n in nodes])
            time.sleep(IG_PAUSE)
    store.save(cache_name(platform, handle), c)
    return c


def details(post):
    need(post["platform"])
    if post["platform"] == "tiktok":
        out = _tiktok_details(post)
    elif post["platform"] == "instagram":
        out = _instagram_details(post)
    else:
        return {}
    changes = {"approx": False}
    if out.get("time"):
        changes["time"] = out["time"]
    if out.get("kind"):
        changes["kind"] = out["kind"]
    if any(post.get(k) != v for k, v in changes.items()):
        fix(post, **changes)
    return out


def _tiktok_details(post):
    name = f"cache/details/tt_{post['id']}.json"
    c = store.load(name)
    if c and time.time() - c.get("at", 0) < 6 * 3600:
        return c
    try:
        with ydl() as y:
            html = y.urlopen(post["url"]).read().decode("utf-8", "replace")
    except Exception:
        raise Unavailable("tiktok wouldn't load that post right now")
    m = re.search(r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}) if m else {}
    item = ((scope.get("webapp.video-detail") or {}).get("itemInfo") or {}).get("itemStruct") or {}
    if not item:
        return {}
    s = item.get("statsV2") or item.get("stats") or {}
    out = {"time": _num(item.get("createTime")), "views": _num(s.get("playCount")), "likes": _num(s.get("diggCount")),
           "comments": _num(s.get("commentCount")), "at": time.time()}
    urls = [((i.get("imageURL") or {}).get("urlList") or [None])[0] for i in (item.get("imagePost") or {}).get("images") or []]
    if any(urls):
        out["kind"] = "photos"
        out["media"] = [["photo", u] for u in urls if u]
    store.save(name, out)
    return out


def _ig_best(options):
    return max(options, key=lambda o: o.get("width") or 0)["url"]


def _ig_media(item):
    media = []
    for part in item.get("carousel_media") or [item]:
        if part.get("video_versions"):
            media.append(["video", _ig_best(part["video_versions"])])
        elif (part.get("image_versions2") or {}).get("candidates"):
            media.append(["photo", _ig_best(part["image_versions2"]["candidates"])])
    return media


def _instagram_details(post):
    name = f"cache/details/instagram_{post['id']}.json"
    c = store.load(name)
    if c and time.time() - c.get("at", 0) < 6 * 3600:
        return c
    with _locks["instagram"]:
        parts = _ig_page(f"p/{post['id']}/")
    info = next((d["xdt_api__v1__media__shortcode__web_info"] for d in parts
                 if d.get("xdt_api__v1__media__shortcode__web_info")), {})
    items = info.get("items") or []
    if not items:
        raise Unavailable("instagram wouldn't load that post right now")
    n = items[0]
    out = {"likes": n.get("like_count"), "comments": n.get("comment_count"), "views": n.get("play_count") or n.get("view_count"),
           "media": _ig_media(n), "time": n.get("taken_at"), "at": time.time()}
    store.save(name, out)
    return out
