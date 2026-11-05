import time

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input, Static

from . import fetch, store
from .browse import Browser
from .widgets import ACCENT, DIM, SOFT, TEXT, TOP, FilterBar, Posts, Preview, exact, link, long_date


def profile_text(prof, platform, added=False):
    t = Text()
    t.append(prof.get("name") or prof["handle"], style="bold " + TOP)
    if prof.get("verified"):
        t.append(" ✓", style=ACCENT)
    t.append(f"   @{prof['handle']} · {platform}", style=DIM)
    t.append("   · saved\n" if added else "\n", style=ACCENT)
    stats = []
    for label, k in (("followers", "followers"), ("following", "following"), ("likes", "likes"), ("posts", "posts")):
        if prof.get(k) is not None:
            stats.append(f"{exact(prof[k])} {label}")
    if stats:
        t.append("  ·  ".join(stats) + "\n", style=TEXT)
    if prof.get("bio"):
        t.append(prof["bio"].strip() + "\n", style=TEXT)
    if prof.get("links"):
        t.append("links  ", style=SOFT)
        for i, l in enumerate(prof["links"][:4]):
            if i:
                t.append("  ·  ", style=SOFT)
            t.append(l.replace("https://", "").replace("http://", ""), style=link(l, DIM))
        t.append("\n")
    if prof.get("locked"):
        t.append("private account. its posts show once the account you connected follows it\n", style=ACCENT)
    return t


class Account(Browser):
    BINDINGS = [
        Binding("plus", "add", "save account", key_display="+"),
        Binding("equals_sign", "add", "save account", show=False),
        Binding("minus", "remove", "unsave", key_display="-"),
        Binding("m", "more", "older posts"),
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self, platform, handle, place=""):
        super().__init__()
        self.platform = platform
        self.handle = handle.lower()
        self.wanted = place
        self.data = None
        self.new = set()
        self.loading_more = False
        self.stop = False
        self.paused = False
        self.more_note = None

    def compose(self):
        yield Static(Text(f"looking up @{self.handle} on {self.platform}…", style=DIM), id="profile")
        with Horizontal(id="body"):
            yield Posts(id="posts")
            yield Preview(id="preview")
        yield FilterBar()
        yield Footer()

    def on_mount(self):
        self.load()

    @work(thread=True, exclusive=True, group="load")
    def load(self, fresh=False):
        try:
            data = fetch.account(self.platform, self.handle, fresh)
        except fetch.NotFound:
            self.app.call_from_thread(self.failed, f"no account called @{self.handle} on {self.platform}")
            return
        except fetch.Unavailable as e:
            self.app.call_from_thread(self.failed, str(e))
            return
        except Exception as e:
            self.app.call_from_thread(self.failed, f"something went wrong: {e}")
            return
        self.app.call_from_thread(self.loaded, data)

    def failed(self, message):
        if self.data:
            self.notify(message, severity="error")
        else:
            self.query_one("#profile", Static).update(Text(message, style=ACCENT))

    def loaded(self, data):
        self.data = data
        self.handle = data["profile"]["handle"]
        self.show_profile()
        self.new = store.unseen(self.platform, self.handle, data["posts"]) | self.new
        self.redraw()
        self.query_one(Posts).focus()
        self.refresh_bindings()
        if self.wanted:
            self.filter_place(self.wanted)
            self.wanted = ""

    def filter_place(self, place):
        if not any(fetch.from_place(p, place) for p in self.data["posts"]):
            self.notify(f"no posts from {place} here, so this is every post", timeout=4)
            return
        self.place = " ".join(place.lower().split())
        bar = self.query_one(FilterBar)
        bar.display = True
        bar.query_one(Input).value = self.place

    def redraw(self):
        if not self.data:
            return
        box = self.query_one(Posts)
        keep = box.current()
        shown = [p for p in self.data["posts"] if self.matches(p)]
        if self.words and not shown:
            footer = "no posts match"
        elif not self.data["posts"]:
            footer = None if self.data["profile"].get("private") else "nothing to see here"
        elif self.loading_more:
            footer = f"loading every post… {self.post_count():,} so far, back to {self.oldest()} · m to pause"
        elif self.more_note:
            footer = f"{self.more_note} · m to try again"
        elif self.data.get("end") and self.platform == "snapchat":
            footer = "that's everything snapchat's public page shares"
        elif self.data.get("end"):
            footer = f"that's every post, back to {self.oldest()}"
        elif self.data.get("short") and not self.paused:
            total = self.data["profile"].get("posts") or 0
            footer = f"{self.platform} only sent {self.post_count():,} of {total:,} posts · m to try again"
        elif self.paused:
            footer = f"paused back at {self.oldest()} · m to keep going"
        else:
            footer = "m loads every older post"
        box.set_posts(shown, new=self.new, footer=footer)
        if keep:
            box.select_id(keep["id"])

    def before_leave(self):
        if self.data and store.is_added(self.platform, self.handle):
            store.mark_seen(self.platform, self.handle, [p["id"] for p in self.data["posts"]])

    def show_profile(self):
        added = store.is_added(self.platform, self.handle)
        prof = dict(self.data["profile"])
        prof["locked"] = prof.get("private") and not self.data["posts"]
        if prof.get("posts") is None and self.data.get("end") and self.platform == "instagram":
            prof["posts"] = self.post_count()
        self.query_one("#profile", Static).update(profile_text(prof, self.platform, added))

    def post_count(self):
        return sum(p["kind"] not in fetch.STORY_KINDS for p in self.data["posts"])

    def oldest(self):
        times = [p["time"] for p in self.data["posts"]
                 if p["time"] and not p.get("pinned") and p["kind"] not in fetch.STORY_KINDS]
        return long_date(min(times)).split(" · ")[0] if times else "the start"

    def action_more(self):
        if self.loading_more:
            self.stop = True
            return
        self.loading_more, self.stop, self.paused, self.more_note = True, False, False, None
        self.redraw()
        self.load_more()

    @work(thread=True, exclusive=True, group="more")
    def load_more(self):
        last = [0]

        def step(c, fresh):
            if store.is_added(self.platform, self.handle):
                store.mark_seen(self.platform, self.handle, [p["id"] for p in fresh])
            if time.time() - last[0] > 1.5:
                last[0] = time.time()
                self.app.call_from_thread(self.progress, c)

        try:
            data, note = fetch.older(self.platform, self.handle, step, lambda: self.stop), None
        except fetch.Unavailable as e:
            data, note = None, str(e)
        except Exception:
            data, note = None, "couldn't load older posts right now"
        self.app.call_from_thread(self.more_done, data, note)

    def progress(self, data):
        self.data = data
        self.redraw()

    def more_done(self, data, note):
        self.loading_more = False
        self.paused = self.stop and not note
        self.more_note = note
        self.data = data or fetch.cached(self.platform, self.handle) or self.data
        self.show_profile()
        self.redraw()
        self.refresh_bindings()

    def check_action(self, action, parameters):
        if action == "more":
            return bool(self.data and self.data["posts"] and not self.data.get("end"))
        if action in ("add", "remove"):
            if not self.data:
                return False
            added = store.is_added(self.platform, self.handle)
            return added if action == "remove" else not added
        return super().check_action(action, parameters)

    def action_add(self):
        store.add(self.platform, self.handle, self.data["profile"].get("name"))
        store.mark_seen(self.platform, self.handle, [p["id"] for p in self.data["posts"]])
        self.notify(f"saved @{self.handle}. it stays on this computer only")
        self.show_profile()
        self.refresh_bindings()

    def action_remove(self):
        store.remove(self.platform, self.handle)
        self.notify(f"unsaved @{self.handle}")
        self.show_profile()
        self.refresh_bindings()

    def action_refresh(self):
        self.load(fresh=True)
