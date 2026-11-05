import http.cookiejar
import json
import re
import struct
import threading
import time
import uuid
from datetime import datetime
from datetime import time as clock
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import instaloader
import requests
import yt_dlp

from . import pb, sessions, store

TTL = 600
IG_EPOCH = 1314220021721
PACIFIC = ZoneInfo("America/Los_Angeles")
POSTED = re.compile(r" on ([A-Z][a-z]+ \d{1,2}, \d{4})")
IG_POSTS_DOC = "28904005159232689"
IG_PAGE = {"Accept": "text/html", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"}
IG_APP = "936619743392459"
IG_SEARCH_DOC = "27706427925724183"
IG_COMMENTS_DOC = "28862333703438672"
IG_REPLIES_DOC = "29434598589503115"
IG_PAUSE = 3
STORY_KINDS = ("story", "highlight")
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


def _tt_headers():
    return {"User-Agent": _agent("tiktok"), "Referer": "https://www.tiktok.com/", "Accept-Language": "en-US,en;q=0.9"}


def media_request(post):
    return {"headers": _tt_headers(), "cookies": sessions.jar("tiktok")} if post["platform"] == "tiktok" else {}


def _tt_json(path, **params):
    try:
        r = requests.get(f"https://www.tiktok.com/api/{path}", cookies=sessions.jar("tiktok"), timeout=20,
                         params={"aid": "1988", **params},
                         headers=_tt_headers())
        return r.json()
    except (requests.RequestException, ValueError):
        raise Unavailable("tiktok wouldn't load that right now, try again in a bit")


def _tt_item_media(item):
    images = (item.get("imagePost") or {}).get("images") or []
    if images:
        urls = [((i.get("imageURL") or {}).get("urlList") or [None])[0] for i in images]
        return [["photo", u] for u in urls if u]
    video = item.get("video") or {}
    url = video.get("playAddr") or video.get("downloadAddr")
    return [["video", url]] if url else []


def _tt_story(handle, prof):
    found, cursor = {}, "0"
    for _ in range(10):
        d = _tt_json("story/item_list/", authorId=prof["uid"], secUid=prof["sec_uid"], count="20", cursor=cursor,
                     loadBackward="false", app_name="tiktok_web", device_platform="web_pc")
        for item in d.get("itemList") or []:
            found[item["id"]] = item
        if not d.get("HasMoreAfter") or not d.get("MaxCursor"):
            break
        cursor = d["MaxCursor"]
    items = sorted(found.values(), key=lambda i: i.get("createTime") or 0)
    media = [m for i in items for m in _tt_item_media(i)]
    if not media:
        return None
    first, last = items[0].get("createTime"), items[-1].get("createTime")
    return {"platform": "tiktok", "handle": handle, "id": f"story-{last}", "url": f"https://www.tiktok.com/@{handle}",
            "time": last, "first": first, "text": "live story", "kind": "story",
            "thumb": (items[-1].get("video") or {}).get("cover") or next((u for k, u in media if k == "photo"), None),
            "views": None, "likes": None, "comments": None, "duration": None, "pinned": False, "media": media,
            "items": len(media), "expires": (last or time.time()) + 86400}


def _with_label(text, label):
    return " ".join(x for x in ((text or "").strip(), label) if x)


def _tt_comment(c):
    label = "(sticker)" if c.get("cmt_sticker_struct") else "(photo)" if c.get("image_list") else None
    return {"id": c.get("cid"), "user": (c.get("user") or {}).get("unique_id") or "?", "text": _with_label(c.get("text"), label),
            "time": c.get("create_time"), "likes": c.get("digg_count"), "replies": c.get("reply_comment_total")}


def _tt_page(d):
    return {"items": [_tt_comment(c) for c in d.get("comments") or []],
            "cursor": str(d["cursor"]) if d.get("has_more") and d.get("cursor") else None, "total": d.get("total")}


def _tt_comments(post, cursor):
    return _tt_page(_tt_json("comment/list/", aweme_id=post["id"], count="20", cursor=cursor or "0"))


def _tt_replies(post, comment, cursor):
    return _tt_page(_tt_json("comment/list/reply/", comment_id=comment["id"], item_id=post["id"], count="20", cursor=cursor or "0"))


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
        "uid": u.get("id"),
        "story": bool(u.get("UserStoryStatus")),
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
        target = f"tiktokuser:{sec_uid}" if sec_uid else f"https://www.tiktok.com/@{handle}"
        try:
            info = y.extract_info(target, download=False)
            entries = list(info.get("entries") or [])
        except yt_dlp.utils.DownloadError as e:
            if prof and prof.get("private"):
                return prof, []
            msg = str(e)
            if "secondary user ID" in msg or "does not exist" in msg:
                raise NotFound(handle)
            raise Unavailable("tiktok wouldn't load that right now, try again in a bit")
    if start == 1 and not prof:
        first = entries[0] if entries else {}
        prof = {"handle": handle, "name": first.get("channel") or handle, "bio": "", "followers": None,
                "following": None, "likes": None, "posts": None, "verified": False, "private": False,
                "sec_uid": None}
    posts = [_tt_post(e, handle) for e in entries]
    if start == 1 and prof.get("story") and prof.get("uid") and prof.get("sec_uid"):
        try:
            story = _tt_story(handle, prof)
        except Unavailable:
            story = None
        if story:
            posts.append(story)
    return prof, posts


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


def _ig_timeline(page_id, handle, cursor=None):
    extra = {"first_data": {"edges": [], "page_info": {"has_next_page": True, "end_cursor": cursor}}} if cursor else {}
    return instaloader.NodeIterator(
        context=_loader.context,
        query_hash=None,
        doc_id=IG_POSTS_DOC,
        web_api=True,
        edge_extractor=lambda d: d["data"]["node"]["polaris_ordered_timeline_connection"],
        node_wrapper=lambda n: n,
        query_variables={"id": page_id, "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False},
        query_referer=f"https://www.instagram.com/{handle}/",
        **extra,
    )


def _ig_private_posts(page_id, handle):
    it = _ig_timeline(page_id, handle)
    nodes = []
    with _locks["instagram"]:
        for n in it:
            nodes.append(n)
            if len(nodes) >= 12:
                break
    page = (getattr(it, "_data", None) or {}).get("page_info") or {}
    return nodes, page.get("end_cursor") if page.get("has_next_page") else None


def instagram_search(query):
    need("instagram")
    variables = {"data": {"context": "blended", "include_reel": "true", "query": query, "rank_token": "",
                          "search_session_id": "", "search_surface": "web_top_search"}, "hasQuery": True}
    with _locks["instagram"]:
        r = _ig_errors(lambda: _loader.context.doc_id_graphql_query(IG_SEARCH_DOC, variables, web_api=True))
    conn = (r.get("data") or {}).get("xdt_api__v1__fbsearch__topsearch_connection")
    if conn is None:
        raise Unavailable("instagram search isn't answering right now")
    users = [u["user"] for u in conn.get("users") or [] if (u.get("user") or {}).get("username")]
    return [{"handle": u["username"].lower(), "name": u.get("full_name") or u["username"],
             "verified": bool(u.get("is_verified")), "context": u.get("search_social_context")} for u in users]


def _ig_comment(n):
    u = n.get("user") or {}
    return {"id": n.get("pk"), "user": u.get("username") or "?", "verified": bool(u.get("is_verified")), "time": n.get("created_at"),
            "text": _with_label(n.get("text"), "(gif)" if n.get("giphy_media_info") else None),
            "likes": n.get("comment_like_count"), "replies": n.get("child_comment_count")}


def _ig_thread(conn):
    page = (conn or {}).get("page_info") or {}
    return {"items": [_ig_comment(e["node"]) for e in (conn or {}).get("edges") or []],
            "cursor": page.get("end_cursor") if page.get("has_next_page") else None}


def _ig_comments(post, cursor):
    d = _instagram_details(post)
    if not cursor:
        first = _ig_first_comments(post)
        return {"items": first["items"], "cursor": first["cursor"], "total": d.get("comments")}
    variables = {"after": cursor, "first": 20, "is_mobile_web": False, "media_id": d["pk"], "sort_order": "popular",
                 "__relay_internal__pv__PolarisIsMobileWebrelayprovider": False,
                 "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
                 "__relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider": False}
    with _locks["instagram"]:
        r = _ig_errors(lambda: _loader.context.doc_id_graphql_query(IG_COMMENTS_DOC, variables, web_api=True))
    conn = ((r.get("data") or {}).get("comments_media") or {}).get("comments_connection")
    if conn is None:
        raise Unavailable("instagram wouldn't send more comments right now")
    return {**_ig_thread(conn), "total": d.get("comments")}


def comments(post, cursor=None):
    need(post["platform"])
    if post["platform"] == "snapchat":
        return _snap_comments(post, cursor)
    return _tt_comments(post, cursor) if post["platform"] == "tiktok" else _ig_comments(post, cursor)


def _ig_replies(comment, cursor):
    variables = {"parent_comment_id": comment["id"], "first": 20, "after": cursor, "before": None, "last": None,
                 "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
                 "__relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider": False}
    with _locks["instagram"]:
        r = _ig_errors(lambda: _loader.context.doc_id_graphql_query(IG_REPLIES_DOC, variables, web_api=True))
    conn = ((r.get("data") or {}).get("fetch__XIGComment") or {}).get("child_comments_connection")
    if conn is None:
        raise Unavailable("instagram wouldn't send the replies right now")
    return _ig_thread(conn)


def replies(post, comment, cursor=None):
    need(post["platform"])
    if post["platform"] == "snapchat":
        return _snap_thread(post, comment["id"], cursor)
    if post["platform"] == "tiktok":
        return _tt_replies(post, comment, cursor)
    return _ig_replies(comment, cursor)


def _ig_thumb(item):
    options = (item.get("image_versions2") or {}).get("candidates") or []
    return min(options, key=lambda o: o.get("width") or 0)["url"] if options else None


def _ig_times(items):
    times = [n["taken_at"] for n in items if n.get("taken_at")]
    return (min(times), max(times)) if times else (None, None)


def _ig_story(handle):
    with _locks["instagram"]:
        parts = _ig_page(f"stories/{handle}/")
    info = next((d["xdt_api__v1__feed__reels_media"] for d in parts if d.get("xdt_api__v1__feed__reels_media")), {})
    reel = next(iter(info.get("reels_media") or []), {})
    items = reel.get("items") or []
    media = [m for n in items for m in _ig_media(n)]
    if not media or ((reel.get("user") or {}).get("username") or handle).lower() != handle:
        return None
    first, last = _ig_times(items)
    ends = [n["expiring_at"] for n in items if n.get("expiring_at")]
    return {"platform": "instagram", "handle": handle, "id": f"story-{reel.get('latest_reel_media') or last}",
            "url": f"https://www.instagram.com/stories/{handle}/", "time": last, "first": first, "approx": False,
            "text": "live story", "kind": "story", "thumb": _ig_thumb(items[-1]), "views": None, "likes": None,
            "comments": None, "duration": None, "pinned": False, "media": media, "items": len(media),
            "expires": max(ends) if ends else last + 86400}


def _ig_highlight_post(node, handle):
    hid = str(node["id"]).removeprefix("highlight:")
    return {"platform": "instagram", "handle": handle, "id": f"hl-{hid}",
            "url": f"https://www.instagram.com/stories/highlights/{hid}/", "time": None, "first": None, "approx": False,
            "text": node.get("title") or "highlight", "kind": "highlight",
            "thumb": node.get("cover_media_cropped_thumbnail_url"), "views": None, "likes": None, "comments": None,
            "duration": None, "pinned": False, "items": None}


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
        "page_id": (timeline or {}).get("id"),
        "cursor": info.get("end_cursor") if info.get("has_next_page") else None,
    }
    posts = [_ig_post(e["node"], handle) for e in conn.get("edges") or []]
    if prof["private"] and user.get("id"):
        try:
            nodes, prof["cursor"] = _ig_private_posts(user["id"], handle)
        except Exception:
            nodes = []
        prof["page_id"] = user["id"]
        posts = [_ig_post(n, handle) for n in nodes]
    edges = (user.get("lox_highlights_connection") or {}).get("edges") or []
    posts += [_ig_highlight_post(e["node"], handle) for e in edges if e["node"].get("id")]
    if user.get("latest_reel_media"):
        try:
            story = _ig_story(handle)
        except (Unavailable, NotFound):
            story = None
        if story:
            posts.append(story)
    return prof, posts


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


def _snap_post(handle, pid, kind, text, thumb, when, media, url, views=None, first=None, comments=None):
    return {"platform": "snapchat", "handle": handle, "id": pid, "url": url, "time": when, "text": text,
            "kind": kind, "thumb": thumb, "views": views, "likes": None, "comments": comments, "duration": None,
            "pinned": False, "media": media, "items": len(media) if kind != "spotlight" else None, "first": first}


def _snap_first(snaps):
    return min((_num(_v(s.get("timestampInSec"))) or 0 for s in snaps), default=0) or None


def _snap_comment(row):
    text = row.get(4, [b""])[0].decode("utf-8", "replace")
    name = row.get(12, [b""])[0].decode("utf-8", "replace")
    return {"id": row.get(1, [None])[0], "name": name or "someone", "text": text,
            "time": row.get(11, [0])[0] // 1000 or None, "replies": row.get(22, [0])[0],
            "likes": sum(pb.decode(r).get(2, [0])[0] for r in row.get(5, []))}


def _snap_thread(post, parent=None, cursor=None):
    m = re.search(r"spotlight/([\w-]+)", post["url"])
    if not m:
        raise Unavailable("snapchat didn't share comments for this one")
    # field numbers from snapchat's public web player. 3 = its web client, 5 = approved, 9 = its usual read mode
    meta = pb.blob(1, str(uuid.uuid4())) + pb.num(2, int(time.time() * 1000)) + pb.num(3, 20)
    body = pb.blob(1, meta) + pb.blob(2, m.group(1)) + pb.num(5, 3) + pb.num(9, 2)
    if cursor:
        body += pb.blob(7, cursor)
    if parent:
        body += pb.blob(8, parent)
    try:
        r = requests.post("https://www.snapchat.com/getreplies", data=body, timeout=20,
                          headers={**UA, "User-Agent": _agent("snapchat"), "Origin": "https://www.snapchat.com"})
        if r.status_code != 200:
            raise Unavailable("snapchat wouldn't send those right now, try again in a bit")
        top = pb.decode(r.content)
    except (requests.RequestException, ValueError, IndexError, struct.error):
        raise Unavailable("snapchat wouldn't send those right now, try again in a bit")
    return {"items": [_snap_comment(pb.decode(x)) for x in top.get(2, [])], "cursor": top.get(4, [None])[0]}


def _snap_comments(post, cursor):
    return {**_snap_thread(post, cursor=cursor), "total": post.get("comments")}


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
                                [["video", vm["contentUrl"]]], s.get("deeplink") or home, _num(vm.get("viewCount")),
                                comments=_num((s.get("engagementStats") or {}).get("commentCount"))))
    taken = set()
    for p in posts:
        while p["id"] in taken:
            p["id"] += "x"
        taken.add(p["id"])
    return prof, posts


def _mark_pinned(posts):
    posts = [p for p in posts if p["kind"] not in STORY_KINDS]
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
    if not c:
        return c
    c["posts"] = [p for p in c["posts"] if not p.get("expires") or p["expires"] > time.time()]
    return _overlay(platform, handle, c)


def _order(posts):
    def top(p):
        return 0 if p["kind"] == "story" else 1 if p["kind"] == "highlight" and p["platform"] == "instagram" else 2

    return sorted(posts, key=lambda p: (top(p), not p.get("pinned"), -(p["time"] or 0)))


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
        c["posts"] = _order(posts + [dict(p, pinned=False) for p in old["posts"]
                                     if p["id"] not in ids and p["kind"] not in STORY_KINDS])
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
            c["end"] = sum(p["kind"] not in STORY_KINDS for p in c["posts"]) >= total
            c["short"] = not c["end"]
    else:
        it = _iters.get(handle)
        if it is None:
            cursor = c.get("cursor") or c["profile"].get("cursor")
            if not cursor:
                c["end"] = True
                store.save(cache_name(platform, handle), c)
                return c
            it = _ig_timeline(c["profile"]["page_id"], handle, cursor)
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
    if post["kind"] == "story" or post["platform"] == "snapchat":
        return {}
    if post["platform"] == "tiktok":
        out = _tiktok_details(post)
    elif post["kind"] == "highlight":
        out = _ig_highlight(post)
    else:
        out = _instagram_details(post)
    changes = {"approx": False}
    for k in ("time", "kind", "first", "items"):
        if out.get(k):
            changes[k] = out[k]
    if "place" in out:
        changes["place"] = out["place"]
    if any(k not in post or post[k] != v for k, v in changes.items()):
        fix(post, **changes)
    return out


def _place_label(name, where):
    return " · ".join(x for x in (name, where) if x) or None


def _tiktok_details(post):
    name = f"cache/details/tiktok_{post['id']}.json"
    c = store.load(name)
    if c and "place" in c and time.time() - c.get("at", 0) < 6 * 3600:
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
    music = item.get("music") or {}
    poi = item.get("poi") or {}
    out = {"time": _num(item.get("createTime")), "views": _num(s.get("playCount")), "likes": _num(s.get("diggCount")),
           "comments": _num(s.get("commentCount")), "at": time.time(),
           "song": " · ".join(x for x in (music.get("title"), music.get("authorName")) if x) or None,
           "place": _place_label(poi.get("name"), poi.get("address") or poi.get("city"))}
    urls = [((i.get("imageURL") or {}).get("urlList") or [None])[0] for i in (item.get("imagePost") or {}).get("images") or []]
    if any(urls):
        out["kind"] = "photos"
        out["media"] = [["photo", u] for u in urls if u]
        out["items"] = len(out["media"])
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


def _ig_highlight(post):
    name = f"cache/details/instagram_{post['id']}.json"
    c = store.load(name)
    if c and time.time() - c.get("at", 0) < 6 * 3600:
        return c
    hid = post["id"].removeprefix("hl-")
    with _locks["instagram"]:
        parts = _ig_page(f"stories/highlights/{hid}/")
    conn = next((d["xdt_api__v1__feed__reels_media__connection"] for d in parts
                 if d.get("xdt_api__v1__feed__reels_media__connection")), {})
    nodes = [e["node"] for e in conn.get("edges") or []]
    node = next((n for n in nodes if str(n.get("id")).endswith(hid)), nodes[0] if nodes else {})
    items = node.get("items") or []
    media = [m for n in items for m in _ig_media(n)]
    if not media:
        raise Unavailable("instagram wouldn't load that highlight right now")
    first, last = _ig_times(items)
    out = {"media": media, "time": last, "first": first, "items": len(media), "at": time.time()}
    store.save(name, out)
    return out


def _ig_song(item):
    meta = [item.get("music_metadata") or {}, item.get("clips_metadata") or {}]
    music = next((m["music_info"] for m in meta if m.get("music_info")), {})
    asset = music.get("music_asset_info") or {}
    if asset.get("title"):
        return " · ".join(x for x in (asset["title"], asset.get("display_artist")) if x)
    sound = next((m["original_sound_info"] for m in meta if m.get("original_sound_info")), None)
    if sound:
        return " · ".join(x for x in (sound.get("original_audio_title") or "Original audio",
                                      (sound.get("ig_artist") or {}).get("username")) if x)
    return None


def _ig_item(post):
    jar = sessions.jar("instagram")
    pk = instaloader.Post.shortcode_to_mediaid(post["id"])
    try:
        r = requests.get(f"https://www.instagram.com/api/v1/media/{pk}/info/", cookies=jar, timeout=20, allow_redirects=False,
                         headers={"User-Agent": _agent("instagram"), "X-IG-App-ID": IG_APP, "X-CSRFToken": jar.get("csrftoken", ""),
                                  "X-Requested-With": "XMLHttpRequest", "Referer": f"https://www.instagram.com/p/{post['id']}/"})
        if r.status_code == 429:
            raise Unavailable("instagram is rate-limiting right now, try again in a few minutes")
        items = r.json().get("items") if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        items = None
    if items:
        return items[0]
    parts = _ig_page(f"p/{post['id']}/")
    info = next((d["xdt_api__v1__media__shortcode__web_info"] for d in parts
                 if d.get("xdt_api__v1__media__shortcode__web_info")), {})
    if not info.get("items"):
        raise Unavailable("instagram wouldn't load that post right now")
    return info["items"][0]


def _instagram_details(post):
    name = f"cache/details/instagram_{post['id']}.json"
    c = store.load(name)
    if c and "place" in c and time.time() - c.get("at", 0) < 6 * 3600:
        return c
    with _locks["instagram"]:
        n = _ig_item(post)
    loc = n.get("location") or {}
    out = {"likes": n.get("like_count"), "comments": n.get("comment_count"),
           "views": n.get("play_count") or n.get("ig_play_count") or n.get("view_count"), "media": _ig_media(n),
           "time": n.get("taken_at"), "pk": str(n.get("pk")), "song": _ig_song(n), "at": time.time(),
           "place": _place_label(loc.get("name"), loc.get("address") or loc.get("city"))}
    store.save(name, out)
    return out


def _ig_first_comments(post):
    name = f"cache/details/instagram_{post['id']}_thread.json"
    c = store.load(name)
    if c and c.get("items") and time.time() - c.get("at", 0) < 6 * 3600:
        return c
    with _locks["instagram"]:
        parts = _ig_page(f"p/{post['id']}/")
    thread = next((d["comments_media"].get("comments_connection") for d in parts if d.get("comments_media")), None)
    out = {**_ig_thread(thread), "at": time.time()}
    if out["items"]:
        store.save(name, out)
    return out
