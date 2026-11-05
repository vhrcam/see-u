from rich.padding import Padding
from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.widgets.option_list import Option

from . import fetch
from .widgets import ACCENT, DIM, SOFT, TEXT, TOP, Nav, ago, exact, first_line


class CommentRows(Nav):
    BINDINGS = [
        Binding("enter", "show_replies", "replies", key_display="enter"),
        Binding("enter", "hide_replies", "hide replies", key_display="enter"),
        Binding("enter", "more_replies", "more replies", key_display="enter"),
    ]

    def check_action(self, action, parameters):
        if action == "select":
            return False
        if action in ("show_replies", "hide_replies", "more_replies"):
            return self.screen.enter_does() == action.removesuffix("_replies")
        return True

    def action_show_replies(self):
        self.action_select()

    def action_hide_replies(self):
        self.action_select()

    def action_more_replies(self):
        self.action_select()


def count(n, one, many):
    return f"{exact(n)} {one if n == 1 else many}"


def comment_text(c):
    t = Text()
    t.append(c.get("name") or f"@{c['user']}", style="bold " + ACCENT)
    if c.get("verified"):
        t.append(" ✓", style=ACCENT)
    meta = [ago(c.get("time"))]
    if c.get("likes"):
        meta.append(count(c["likes"], "like", "likes"))
    if c.get("replies"):
        meta.append(count(c["replies"], "reply", "replies"))
    t.append("   " + " · ".join(m for m in meta if m) + "\n", style=DIM)
    t.append(((c.get("text") or "").strip() or "(no text)") + "\n", style="bold " + TEXT)
    return t


def indented(text):
    return Padding(text, (0, 0, 0, 4))


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
        self.rows = []

    def compose(self):
        yield Static(id="commenthead")
        yield CommentRows(id="commentbody")
        yield Footer()

    def on_mount(self):
        self.show()
        self.query_one(CommentRows).focus()
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
        if not self.is_attached:
            return
        self.waiting, self.note = False, note
        if page:
            self.items += page["items"]
            self.cursor = page["cursor"]
            if page.get("total") is not None:
                self.total = page["total"]
        self.show()

    @work(thread=True, group="replies")
    def load_replies(self, c):
        try:
            page, note = fetch.replies(self.post, c, c["thread"]["cursor"]), None
        except fetch.Unavailable as e:
            page, note = None, str(e)
        except Exception:
            page, note = None, "couldn't load replies right now"
        self.app.call_from_thread(self.replies_loaded, c, page, note)

    def replies_loaded(self, c, page, note):
        if not self.is_attached:
            return
        t = c["thread"]
        t["waiting"], t["note"] = False, note
        if page:
            t["items"] += page["items"]
            t["cursor"] = page["cursor"]
        self.show()

    def thread_rows(self, c):
        t = c["thread"]
        rows = [{"type": "reply", "comment": c, "reply": r, "prompt": indented(comment_text(r))} for r in t["items"]]
        if t["note"]:
            rows.append({"type": "status", "prompt": indented(Text(t["note"] + "\n", style=ACCENT))})
        if t["waiting"]:
            rows.append({"type": "status", "prompt": indented(Text("loading replies…\n", style=SOFT))})
        elif t["cursor"]:
            rows.append({"type": "more", "comment": c, "prompt": indented(Text("more replies\n", style=ACCENT))})
        elif not t["items"] and not t["note"]:
            rows.append({"type": "status", "prompt": indented(Text(f"{self.post['platform']} didn't share these\n", style=SOFT))})
        return rows

    def all_rows(self):
        rows = []
        for c in self.items:
            rows.append({"type": "comment", "comment": c, "prompt": comment_text(c)})
            if c.get("thread") and c["thread"]["open"]:
                rows += self.thread_rows(c)
        if self.note:
            rows.append({"type": "status", "prompt": Text(self.note, style=ACCENT)})
        elif self.waiting:
            rows.append({"type": "status", "prompt": Text("loading comments…", style=SOFT)})
        elif not self.items:
            rows.append({"type": "status", "prompt": Text("no comments", style=SOFT)})
        elif self.cursor:
            rows.append({"type": "status", "prompt": Text("m loads more", style=SOFT)})
        else:
            rows.append({"type": "status", "prompt": Text("that's every comment it sent", style=SOFT)})
        return rows

    def current(self):
        i = self.query_one(CommentRows).highlighted
        return self.rows[i] if i is not None and i < len(self.rows) else None

    def spot_for(self, old):
        if not old:
            return next((i for i, r in enumerate(self.rows) if r["type"] != "status"), None)
        same = lambda r: r.get("comment") is old.get("comment")
        for i, r in enumerate(self.rows):
            if r["type"] == old["type"] and same(r) and r.get("reply") is old.get("reply"):
                return i
        replies = [i for i, r in enumerate(self.rows) if r["type"] == "reply" and same(r)]
        if old["type"] == "more" and replies:
            return replies[-1]
        return next((i for i, r in enumerate(self.rows) if r["type"] == "comment" and same(r)), None)

    def show(self):
        p = self.post
        head = Text()
        head.append("comments", style="bold " + TOP)
        head.append(f"   @{p['handle']} · {p['platform']}", style=DIM)
        if self.total is not None:
            head.append(f" · {exact(self.total)} in all", style=DIM)
        if self.items:
            head.append(f" · showing {exact(len(self.items))}", style=DIM)
        caption = first_line(p.get("text"), 110)
        if caption:
            head.append(f"\n{caption}", style=SOFT)
        self.query_one("#commenthead", Static).update(head)
        rows = self.query_one(CommentRows)
        old, y = self.current(), rows.scroll_y
        self.rows = self.all_rows()
        rows.clear_options()
        rows.add_options([Option(r["prompt"], disabled=r["type"] == "status") for r in self.rows])
        rows.highlighted = self.spot_for(old)
        rows.scroll_to(y=y, animate=False)
        self.refresh_bindings()

    def on_option_list_option_highlighted(self, event):
        self.refresh_bindings()

    def on_option_list_option_selected(self, event):
        action = self.enter_does()
        if not action:
            return
        c = self.current()["comment"]
        t = c.setdefault("thread", {"items": [], "cursor": None, "open": False, "waiting": False, "note": None})
        t["open"] = action != "hide"
        if t["open"] and (action == "more" or not t["items"]) and not t["waiting"]:
            t["waiting"], t["note"] = True, None
            self.load_replies(c)
        self.show()

    def enter_does(self):
        row = self.current()
        if not row:
            return None
        if row["type"] == "more":
            return "more"
        if row["type"] == "comment" and row["comment"].get("replies"):
            t = row["comment"].get("thread")
            return "hide" if t and t["open"] else "show"
        return None

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
