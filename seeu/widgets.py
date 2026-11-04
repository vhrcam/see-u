import math
import re
import time
from datetime import datetime

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

ACCENT = "#22d3ee"
TEXT = "#67e8f9"
BRIGHT = "#a5f3fc"
WHITE = "#ecfeff"
DIM = "#0e7490"
SOFT = "#155e75"
KIND = {"video": "video", "reel": "reel", "photo": "photo", "carousel": "album", "story": "story",
        "highlight": "highlight", "spotlight": "spotlight", "photos": "photos"}
TAG = {"tiktok": "tt", "instagram": "ig", "snapchat": "sc"}


def web_url(url):
    url = (url or "").strip()
    if re.match(r"https?://", url, re.I):
        return url
    return None if not url or "://" in url else f"https://{url}"


def link(url, color):
    return Style(color=color, underline=True, meta={"@click": f"app.open_link({url!r})"})


def human(n):
    if n is None:
        return "?"
    if n < 10000:
        return f"{n:,}"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            if v >= 100:
                return f"{int(v)}{suffix}"
            return f"{math.floor(v * 10) / 10:.1f}".rstrip("0").rstrip(".") + suffix


def exact(n):
    return "?" if n is None else f"{n:,}"


def short_date(ts):
    if not ts:
        return "?"
    d = datetime.fromtimestamp(ts)
    return f"{d:%b} {d.day}" if d.year == datetime.now().year else f"{d:%b} {d.day} '{d:%y}"


def day_only(ts):
    if not ts:
        return "unknown date"
    d = datetime.fromtimestamp(ts)
    return f"{d:%b} {d.day}, {d.year}"


def long_date(ts):
    if not ts:
        return "unknown date"
    d = datetime.fromtimestamp(ts)
    return f"{day_only(ts)} · {d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def short_ago(ts):
    s = max(0, time.time() - ts)
    for size, unit in ((31536000, "y"), (2592000, "mo"), (604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return "now"


def ago(ts, rough=False):
    if not ts:
        return ""
    if rough:
        days = (datetime.now().date() - datetime.fromtimestamp(ts).date()).days
        if days < 7:
            return "today" if days <= 0 else "yesterday" if days == 1 else f"{days}d ago"
    s = max(0, time.time() - ts)
    for size, unit in ((31536000, "y"), (2592000, "mo"), (604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if s >= size:
            return f"{int(s // size)}{unit} ago"
    return "just now"


def first_line(text, limit=140):
    line = next((l for l in (text or "").splitlines() if l.strip()), "")
    line = re.sub(r"\s+", " ", line).strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def post_line(p, new=False, who=0):
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append("● " if new else "  ", style="bold " + WHITE)
    t.append(f"{short_date(p['time']):<10}", style=DIM)
    if who:
        t.append(f"{TAG[p['platform']]} ", style=SOFT)
        t.append(f"@{p['handle'][:who - 2]:<{who}}", style=ACCENT)
    t.append(f"{KIND.get(p['kind'], p['kind']):<10}", style=SOFT)
    if p.get("pinned"):
        t.append("pinned ", style=DIM)
    t.append(first_line(p["text"]) or "(no caption)", style=WHITE if new else TEXT)
    return t


class Nav(OptionList):
    BINDINGS = [
        Binding("w", "cursor_up", "up", show=False),
        Binding("s", "cursor_down", "down", show=False),
        Binding("enter", "select", "open", key_display="enter"),
    ]
    armed = None

    def check_action(self, action, parameters):
        if action == "select":
            return self.highlighted is not None
        return True

    async def _on_click(self, event):
        event.prevent_default()
        clicked = event.style.meta.get("option")
        if clicked is None or self._options[clicked].disabled:
            return
        if clicked == self.armed and self.highlighted == clicked:
            self.armed = None
            self.action_select()
        else:
            self.armed = clicked
            self.highlighted = clicked


class Posts(Nav):
    def on_mount(self):
        self.by_id = {}

    def refresh_post(self, p):
        new, width = getattr(self, "ctx", ((), 0))
        for oid, q in self.by_id.items():
            if q["id"] == p["id"] and q["platform"] == p["platform"]:
                self.replace_option_prompt(oid, post_line(p, p["id"] in new, width))

    def set_posts(self, posts, new=(), who=False, caught_up=None, footer=None):
        self.by_id = {}
        options = []
        width = min(20, max((len(p["handle"]) for p in posts), default=0) + 2) if who else 0
        self.ctx = (new, width)
        if caught_up == -1 and posts:
            options.append(Option(Text("   ── you're caught up ──", style=DIM), disabled=True))
        for i, p in enumerate(posts):
            oid = f"p{i}"
            self.by_id[oid] = p
            options.append(Option(post_line(p, p["id"] in new, width), id=oid))
            if caught_up is not None and i == caught_up:
                options.append(Option(Text("   ── you're caught up ──", style=DIM), disabled=True))
        if footer:
            options.append(Option(Text(f"   ── {footer} ──", style=SOFT), disabled=True))
        self.clear_options()
        self.add_options(options)
        for preview in self.screen.query(Preview):
            preview.display = bool(posts)
        self.screen.refresh_bindings()
        if posts:
            self.highlighted = next(i for i, o in enumerate(options) if not o.disabled)

    def at_top(self):
        if self.highlighted is None:
            return True
        return all(self.get_option_at_index(i).disabled for i in range(self.highlighted))

    def select_id(self, pid):
        for i in range(self.option_count):
            o = self.get_option_at_index(i)
            if o.id and self.by_id.get(o.id, {}).get("id") == pid:
                self.highlighted = i
                return

    def current(self):
        if self.highlighted is None:
            return None
        return self.by_id.get(self.get_option_at_index(self.highlighted).id)


def post_text(p, extra=None):
    extra = extra or {}
    t = Text()
    kind = extra.get("kind") or p["kind"]
    t.append(f"@{p['handle']}", style="bold " + ACCENT)
    t.append(f"  {p['platform']} · {KIND.get(kind, kind)}", style=DIM)
    if p.get("pinned"):
        t.append(" · pinned", style=DIM)
    t.append("\n")
    when = extra.get("time") or p["time"]
    approx = bool(p.get("approx") and not extra.get("time"))
    first = p.get("first")
    if first and when and datetime.fromtimestamp(first).date() != datetime.fromtimestamp(when).date():
        f = datetime.fromtimestamp(first)
        start = f"{f:%b} {f.day}" if f.year == datetime.fromtimestamp(when).year else day_only(first)
        t.append(f"{start} → {long_date(when)}", style=TEXT)
    elif approx:
        t.append(day_only(when), style=TEXT)
        if p["platform"] == "instagram" and not p.get("saved_dir"):
            t.append("   exact time loading…", style=SOFT)
    else:
        t.append(long_date(when), style=TEXT)
    t.append(f"   {ago(when, approx)}\n", style=SOFT)
    stats = []
    for label, k in (("views", "views"), ("likes", "likes"), ("comments", "comments")):
        v = extra.get(k, p.get(k))
        if v is not None:
            stats.append(f"{exact(v)} {label}")
    if p.get("duration"):
        stats.append(f"{int(p['duration'])}s")
    if p.get("items"):
        stats.append(f"{p['items']} snaps")
    if stats:
        t.append("  ·  ".join(stats) + "\n", style=TEXT)
    elif p["platform"] == "instagram" and not p.get("saved_dir"):
        t.append("loading likes…\n", style=SOFT)
    if p.get("saved_dir"):
        t.append(f"saved in {p['folder']}\n", style=SOFT)
    t.append("\n")
    t.append((p["text"] or "(no caption)").strip() + "\n", style=WHITE)
    t.append("\n")
    t.append(p["url"].replace("https://www.", ""), style=link(p["url"], DIM))
    return t


class FilterBar(Horizontal):
    def compose(self):
        yield Static(Text("/ ", style="bold " + ACCENT), classes="slash")
        yield Input(placeholder="filter captions, handles, or post types", id="filter")

    def on_mount(self):
        self.display = False


class Preview(VerticalScroll):
    def compose(self):
        yield Static(id="thumb")
        yield Static(id="info")

    def show(self, p, extra=None):
        self.query_one("#info", Static).update(post_text(p, extra) if p else "")
        self.scroll_home(animate=False)

    def on_mount(self):
        self.query_one("#thumb", Static).display = False

    def picture(self, visual):
        thumb = self.query_one("#thumb", Static)
        thumb.update(visual or "")
        thumb.display = bool(visual)
