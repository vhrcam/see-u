import time

from textual import work
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Input

from . import fetch, media
from .comments import Comments
from .widgets import KIND, FilterBar, Posts, Preview, item_word


class Browser(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("slash", "filter", "filter", key_display="/"),
        Binding("v", "save", "save post"),
        Binding("k", "comments", "comments"),
        Binding("o", "open_link", "open link"),
        Binding("c", "copy", "copy link"),
    ]

    def __init__(self):
        super().__init__()
        self.extra = {}
        self.current = None
        self.opening = set()
        self.words = []
        self.leaving = False

    def check_action(self, action, parameters):
        posts = self.query_one(Posts)
        if action in ("copy", "open_link", "save", "open", "delete"):
            return posts.current() is not None
        if action == "comments":
            p = posts.current()
            return bool(p and p["kind"] not in fetch.STORY_KINDS and not p.get("saved_dir"))
        if action == "filter":
            return posts.option_count > 0 or bool(self.words)
        return True

    def matches(self, p):
        hay = f"{p.get('text') or ''} @{p['handle']} {p['platform']} {p['kind']} {p.get('folder', '')}".lower()
        return all(w in hay for w in self.words)

    def redraw(self):
        pass

    def before_leave(self):
        pass

    def on_option_list_option_highlighted(self, event):
        p = self.query_one(Posts).current()
        self.current = p
        preview = self.query_one(Preview)
        self.refresh_bindings()
        preview.show(p, self.extra.get(p["id"]) if p else None)
        preview.picture(None)
        if p:
            self.load_thumb(p, max(10, min(44, preview.size.width - 4)))
        if p and p["platform"] in ("instagram", "tiktok") and p["id"] not in self.extra and not p.get("saved_dir"):
            self.load_details(p)

    def thumb_for(self, p, cols):
        return media.thumbnail(p, cols, 15)

    @work(thread=True, exclusive=True, group="thumb")
    def load_thumb(self, p, cols):
        time.sleep(0.15)
        if self.current is not p:
            return
        try:
            pic = self.thumb_for(p, cols)
        except Exception:
            pic = None
        if self.current is p:
            self.app.call_from_thread(self.query_one(Preview).picture, pic)

    @work(thread=True, exclusive=True, group="details")
    def load_details(self, p):
        time.sleep(0.8)
        if self.current is not p:
            return
        try:
            self.extra[p["id"]] = fetch.details(p)
        except Exception:
            self.extra[p["id"]] = {}
        self.app.call_from_thread(self.fixed, p)

    def fixed(self, p):
        if self.leaving:
            return
        self.query_one(Posts).refresh_post(p)
        if self.current is p:
            self.query_one(Preview).show(p, self.extra.get(p["id"]))

    def on_option_list_option_selected(self, event):
        self.action_open()

    def action_open(self):
        p = self.query_one(Posts).current()
        if p:
            self.open_post(p)

    def view(self, p):
        media.view(p, (self.extra.get(p["id"]) or {}).get("media"))

    @work(thread=True, group="open")
    def open_post(self, p):
        if p["id"] in self.opening:
            return
        self.opening.add(p["id"])
        many = (p.get("items") or 0) > media.VIEW_LIMIT
        note = f"opening the first {media.VIEW_LIMIT} of {p['items']} {item_word(p)}…" if many else f"opening the {KIND.get(p['kind'], 'post')}…"
        self.app.call_from_thread(self.notify, note, timeout=3)
        try:
            self.view(p)
        except Exception:
            self.app.call_from_thread(self.notify, "couldn't open that one right now", severity="error")
        finally:
            self.opening.discard(p["id"])

    def action_save(self):
        from .saved import FolderPick

        p = self.query_one(Posts).current()
        if p:
            self.app.push_screen(FolderPick(), lambda folder: folder and self.save_post(p, folder))

    @work(thread=True, group="save")
    def save_post(self, p, folder):
        self.app.call_from_thread(self.notify, f"saving to {folder}…", timeout=3)
        try:
            media.save(p, folder, self.extra.get(p["id"]))
            self.app.call_from_thread(self.notify, f"saved to {folder}. it works offline now")
        except Exception:
            self.app.call_from_thread(self.notify, "couldn't save that one right now", severity="error")

    def action_comments(self):
        p = self.query_one(Posts).current()
        if p:
            self.app.push_screen(Comments(p))

    def action_open_link(self):
        p = self.query_one(Posts).current()
        if p:
            self.app.action_open_link(p["url"])

    def action_copy(self):
        p = self.query_one(Posts).current()
        if p:
            if media.copy(p["url"]):
                self.notify("link copied", timeout=2)
            else:
                self.notify(p["url"], title="no clipboard tool found, here's the link", timeout=10)

    def action_filter(self):
        bar = self.query_one(FilterBar)
        bar.display = True
        bar.query_one(Input).focus()

    def on_input_changed(self, event):
        if event.input.id == "filter":
            self.words = event.value.lower().split()
            self.redraw()

    def on_input_submitted(self, event):
        if event.input.id == "filter":
            self.query_one(Posts).focus()

    def action_back(self):
        bar = self.query_one(FilterBar)
        if bar.display:
            bar.query_one(Input).value = ""
            bar.display = False
            self.words = []
            self.redraw()
            self.query_one(Posts).focus()
            return
        self.leaving = True
        self.before_leave()
        self.app.pop_screen()
