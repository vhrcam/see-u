from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Static

from . import fetch, store
from .browse import Browser
from .widgets import ACCENT, DIM, SOFT, TOP, FilterBar, Posts, Preview


class Feed(Browser):
    BINDINGS = [
        Binding("r", "refresh", "refresh"),
    ]

    def __init__(self):
        super().__init__()
        self.collected = []
        self.posts = []
        self.new = set()
        self.failed = []
        self.done = 0
        self.total = 0

    def compose(self):
        yield Static(id="feedhead")
        with Horizontal(id="body"):
            yield Posts(id="posts")
            yield Preview(id="preview")
        yield FilterBar()
        yield Footer()

    def on_mount(self):
        self.load()

    @work(thread=True, exclusive=True, group="load")
    def load(self, fresh=False):
        accounts = store.accounts()
        self.total, self.done, self.failed = len(accounts), 0, []
        collected = []
        first = not self.collected
        self.app.call_from_thread(self.show if first else self.update_head, *([[]] if first else []))
        for a in accounts:
            try:
                posts = fetch.account(a["platform"], a["handle"], fresh)["posts"]
                self.new |= store.unseen(a["platform"], a["handle"], posts)
                collected += posts
            except Exception:
                self.failed.append(f"@{a['handle']} ({a['platform']})")
            self.done += 1
            if first:
                self.app.call_from_thread(self.show, list(collected))
            else:
                self.app.call_from_thread(self.update_head)
        if not first:
            self.app.call_from_thread(self.show, list(collected))

    def update_head(self):
        self.query_one("#feedhead", Static).update(self.head())

    def head(self):
        t = Text()
        t.append("feed", style="bold " + TOP)
        t.append(f"   {self.total} accounts · {len(self.collected)} posts", style=DIM)
        fresh = sum(p["id"] in self.new for p in self.collected)
        t.append(f" · {fresh} new", style="bold " + TOP if fresh else DIM)
        if self.words:
            t.append(f"   {len(self.posts)} match", style=ACCENT)
        if self.done < self.total:
            t.append(f"   loading {self.done + 1} of {self.total}…", style=SOFT)
        if self.failed:
            t.append("\ncouldn't load " + ", ".join(self.failed), style=ACCENT)
        if not self.total:
            t.append("\nsave accounts first: look one up with : on the home screen, then press +", style=SOFT)
        return t

    def show(self, posts):
        self.collected = posts
        self.redraw()
        if not self.words:
            self.query_one(Posts).focus()

    def redraw(self):
        newest = lambda p: -(p["time"] or 0)
        shown = [p for p in self.collected if self.matches(p)]
        fresh = sorted((p for p in shown if p["id"] in self.new), key=newest)
        old = sorted((p for p in shown if p["id"] not in self.new), key=newest)
        self.posts = fresh + old
        self.query_one("#feedhead", Static).update(self.head())
        box = self.query_one(Posts)
        keep = None if box.at_top() else box.current()
        caught = None if self.words or not self.posts else (len(fresh) - 1 if fresh else -1)
        footer = "no posts match" if self.words and not self.posts else None
        box.set_posts(self.posts, new=self.new, who=True, caught_up=caught, footer=footer)
        if keep:
            box.select_id(keep["id"])

    def before_leave(self):
        groups = {}
        for p in self.collected:
            groups.setdefault((p["platform"], p["handle"]), []).append(p["id"])
        for (platform, handle), ids in groups.items():
            if store.is_added(platform, handle):
                store.mark_seen(platform, handle, ids)

    def action_refresh(self):
        self.load(fresh=True)
