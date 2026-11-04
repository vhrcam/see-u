from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from . import fetch
from .widgets import ACCENT, DIM, SOFT, WHITE, ago, exact, first_line


class CommentList(VerticalScroll):
    BINDINGS = [
        Binding("w", "scroll_up", "up", show=False),
        Binding("s", "scroll_down", "down", show=False),
    ]


def comment_text(c):
    t = Text()
    t.append(f"@{c['user']}", style="bold " + ACCENT)
    if c.get("verified"):
        t.append(" ✓", style=ACCENT)
    meta = [ago(c.get("time"))]
    if c.get("likes"):
        meta.append(f"{exact(c['likes'])} likes")
    if c.get("replies"):
        meta.append(f"{exact(c['replies'])} replies")
    t.append("   " + " · ".join(m for m in meta if m) + "\n", style=DIM)
    t.append((c.get("text") or "").strip() + "\n\n", style=WHITE)
    return t


class Comments(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("m", "more", "more comments"),
    ]

    def __init__(self, post):
        super().__init__()
        self.post = post
        self.items = []
        self.cursor = None
        self.total = None
        self.waiting = True
        self.note = None

    def compose(self):
        yield Static(id="commenthead")
        with CommentList(id="commentbody"):
            yield Static(id="commentlist")
        yield Footer()

    def on_mount(self):
        self.show()
        self.query_one("#commentbody").focus()
        self.load()

    @work(thread=True, exclusive=True, group="comments")
    def load(self):
        try:
            page, note = fetch.comments(self.post, self.cursor), None
        except fetch.Unavailable as e:
            page, note = None, str(e)
        except Exception:
            page, note = None, "couldn't load comments right now"
        self.app.call_from_thread(self.loaded, page, note)

    def loaded(self, page, note):
        self.waiting, self.note = False, note
        if page:
            self.items += page["items"]
            self.cursor = page["cursor"]
            if page.get("total") is not None:
                self.total = page["total"]
        self.show()

    def show(self):
        p = self.post
        head = Text()
        head.append("comments", style="bold " + WHITE)
        head.append(f"   @{p['handle']} · {p['platform']}", style=DIM)
        if self.total is not None:
            head.append(f" · {exact(self.total)} in all", style=DIM)
        if self.items:
            head.append(f" · showing {exact(len(self.items))}", style=DIM)
        caption = first_line(p.get("text"), 110)
        if caption:
            head.append(f"\n{caption}", style=SOFT)
        self.query_one("#commenthead", Static).update(head)
        body = Text()
        for c in self.items:
            body.append_text(comment_text(c))
        if self.note:
            body.append(self.note + "\n", style=ACCENT)
        elif self.waiting:
            body.append("loading comments…\n", style=SOFT)
        elif not self.items:
            body.append("no comments\n", style=SOFT)
        elif self.cursor:
            body.append("m loads more\n", style=SOFT)
        else:
            body.append("that's every comment it sent\n", style=SOFT)
        self.query_one("#commentlist", Static).update(body)
        self.refresh_bindings()

    def check_action(self, action, parameters):
        if action == "more":
            return bool(self.cursor) and not self.waiting
        return True

    def action_more(self):
        self.waiting, self.note = True, None
        self.show()
        self.load()

    def action_back(self):
        self.app.pop_screen()
