import atexit
import random

from rich.cells import set_cell_size
from rich.text import Text
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import fetch, media, sessions, signin, store
from .account import Account
from .eye import blink, eye, glance, shimmer
from .feed import Feed
from .logins import Logins, lights
from .saved import Saved
from .search import PLATFORMS, Results
from .widgets import ACCENT, DIM, KIND, SOFT, TAG, TEXT, WHITE, Nav, short_ago, short_date, web_url

THEME = Theme(
    name="see-u",
    primary="#22d3ee",
    secondary="#0891b2",
    accent="#67e8f9",
    foreground="#67e8f9",
    background="#000000",
    surface="#000000",
    panel="#06222a",
    boost="#083344",
    warning="#a5f3fc",
    error="#a5f3fc",
    success="#22d3ee",
    dark=True,
    variables={
        "footer-background": "#000000",
        "footer-key-foreground": "#22d3ee",
        "footer-description-foreground": "#0e7490",
        "block-cursor-background": "#083344",
        "block-cursor-foreground": "#cffafe",
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": "#083344",
        "block-cursor-blurred-foreground": "#cffafe",
        "block-cursor-blurred-text-style": "none",
        "block-hover-background": "#04161b",
        "input-cursor-background": "#22d3ee",
        "input-selection-background": "#0e7490",
        "scrollbar": "#0e7490",
        "scrollbar-hover": "#22d3ee",
        "scrollbar-active": "#67e8f9",
        "scrollbar-background": "#000000",
        "scrollbar-background-hover": "#000000",
        "scrollbar-background-active": "#000000",
        "border": "#0e7490",
        "border-blurred": "#083344",
    },
)


def title(accounts=0, new=0, saved=0):
    t = Text()
    t.append("\n")
    t.append("      s  e  e\n", style="bold #a5f3fc")
    t.append("                  u\n", style="bold #22d3ee")
    t.append("\n")
    t.append("  ")
    t.append_text(lights())
    t.append("\n\n")
    t.append(f"{accounts} accounts", style="#67e8f9")
    t.append("  ·  ", style="#0e7490")
    t.append(f"{new} new", style="bold #a5f3fc" if new else "#67e8f9")
    t.append("  ·  ", style="#0e7490")
    t.append(f"{saved} saved posts", style="#67e8f9")
    return t


def newest(a, limit=40):
    c = fetch.cached(a["platform"], a["handle"]) or {}
    posts = [p for p in c.get("posts") or [] if p["time"]]
    fresh = store.unseen(a["platform"], a["handle"], posts)
    posts.sort(key=lambda p: -p["time"])
    t = Text(no_wrap=True, overflow="ellipsis")
    if not posts:
        t.append("loading…" if not c else "no posts", style=SOFT)
    for p in posts[:limit]:
        new = p["id"] in fresh
        t.append("● " if new else "  ", style="bold " + WHITE)
        t.append(f"{short_ago(p['time']):>4}   ", style=DIM)
        t.append(f"{KIND.get(p['kind'], p['kind']):<11}", style=ACCENT if new else TEXT)
        t.append(f"{short_date(p['time'])}\n", style=SOFT)
    return t


def new_count(a):
    c = fetch.cached(a["platform"], a["handle"]) or {}
    return len(store.unseen(a["platform"], a["handle"], c.get("posts") or []))


def account_line(a, new=0):
    c = fetch.cached(a["platform"], a["handle"]) or {}
    name = (c.get("profile") or {}).get("name") or a.get("name") or a["handle"]
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f"  {set_cell_size(name, 16)}  ", style=WHITE)
    t.append(f"@{a['handle'][:16]:<18}", style=ACCENT)
    t.append(f"{TAG[a['platform']]:<4}", style=DIM)
    if new:
        t.append(f"{new} new", style="bold " + WHITE)
    return t


class Home(Screen):
    AUTO_FOCUS = ""

    BINDINGS = [
        Binding("colon", "search", "search", show=False),
        Binding("slash", "command", "command", show=False),
        Binding("tab", "platform", "platform", priority=True),
        Binding("f", "feed", "feed"),
        Binding("v", "saved", "saved posts"),
        Binding("l", "logins", "logins"),
        Binding("ctrl+l", "logins_typing", "logins", key_display="ctrl+l"),
        Binding("r", "refresh", "refresh"),
        Binding("x", "remove", "unsave"),
        Binding("escape", "leave_search", "done typing", show=False),
        Binding("q", "quit_list", "quit"),
        Binding("ctrl+q", "quit_typing", "quit", key_display="ctrl+q"),
    ]

    def __init__(self):
        super().__init__()
        self.platform = PLATFORMS[0]
        self.rows = []

    def compose(self):
        with Horizontal(id="banner"):
            yield Static(eye(), id="eye")
            yield Static(title(), id="title")
        with Horizontal(id="homebody"):
            with Vertical(id="left"):
                yield Static(Text("saved accounts", style=DIM), classes="paneltitle")
                yield Nav(id="accounts")
                yield Static(id="hint")
            with Vertical(id="right"):
                yield Static(id="newesttitle", classes="paneltitle")
                yield Static(id="newest")
        with Horizontal(id="searchbar"):
            yield Static(id="where")
            yield Input(placeholder="handle/profile link", id="search", select_on_focus=False)
        yield Footer()

    def on_mount(self):
        self.show_platform()
        self.fill(focus=True)
        self.refresh_all()
        self.check_logins()
        self.set_timer(random.uniform(3, 6), self.idle)

    def idle(self):
        if self.app.screen is not self:
            self.set_timer(5, self.idle)
            return
        move = random.choices(["blink", "glance", "shimmer"], weights=[5, 3, 2])[0]
        frames, step = blink() if move == "blink" else glance(random.choice((-1, 1))) if move == "glance" else shimmer()
        self.pose(frames, step)

    def pose(self, frames, step):
        self.query_one("#eye", Static).update(eye(**frames[0]))
        if len(frames) > 1:
            self.set_timer(step, lambda: self.pose(frames[1:], step))
        else:
            self.set_timer(random.uniform(5, 11), self.idle)

    def on_screen_resume(self):
        self.fill(focus=True)
        self.refresh_all()

    def fill(self, focus=False):
        self.rows = store.accounts()
        box = self.query_one("#accounts", OptionList)
        keep = box.highlighted
        counts = [new_count(a) for a in self.rows]
        box.clear_options()
        box.add_options([Option(account_line(a, n), id=f"a{i}") for i, (a, n) in enumerate(zip(self.rows, counts))])
        box.display = bool(self.rows)
        box.highlighted = min(keep, len(self.rows) - 1) if self.rows and keep is not None else None
        hint = "" if self.rows else ("type a handle below to look someone up. tab switches the platform."
                                     if sessions.connected() else "press l to connect an account and get started.")
        self.query_one("#hint", Static).update(Text(hint, style=DIM))
        self.query_one("#hint", Static).display = not self.rows
        self.query_one("#right").display = bool(self.rows)
        self.show_newest()
        self.query_one("#title", Static).update(title(len(self.rows), sum(counts), len(media.saved())))
        if focus:
            if self.rows:
                box.focus()
            elif sessions.connected():
                self.query_one("#search", Input).focus()
            else:
                self.set_focus(None)
        self.refresh_bindings()

    @work(thread=True, exclusive=True, group="refresh")
    def refresh_all(self, fresh=False):
        for a in store.accounts():
            try:
                fetch.account(a["platform"], a["handle"], fresh)
            except Exception:
                continue
            self.app.call_from_thread(self.fill)

    @work(thread=True, group="logins")
    def check_logins(self):
        if signin.recheck():
            self.app.call_from_thread(self.fill)

    def check_action(self, action, parameters):
        typing = isinstance(self.focused, Input)
        if action == "remove":
            return bool(self.rows) and not typing and self.query_one("#accounts", OptionList).highlighted is not None
        if action in ("quit_list", "feed", "saved", "refresh", "logins"):
            return not typing
        if action in ("quit_typing", "leave_search", "logins_typing"):
            return typing
        return True

    def action_quit_list(self):
        self.app.exit()

    def action_quit_typing(self):
        self.app.exit()

    def on_descendant_focus(self, event):
        self.refresh_bindings()

    def show_newest(self):
        box = self.query_one("#accounts", OptionList)
        if not self.rows or box.highlighted is None or box.highlighted >= len(self.rows):
            self.query_one("#newesttitle", Static).update(Text("newest", style=DIM))
            self.query_one("#newest", Static).update("")
            return
        a = self.rows[box.highlighted]
        head = Text()
        head.append("newest from ", style=DIM)
        head.append(f"@{a['handle']}", style=ACCENT)
        head.append(f"  {a['platform']}", style=SOFT)
        self.query_one("#newesttitle", Static).update(head)
        self.query_one("#newest", Static).update(newest(a))

    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == "accounts":
            self.show_newest()
            self.refresh_bindings()

    def on_option_list_option_selected(self, event):
        a = self.rows[event.option_index]
        self.app.push_screen(Account(a["platform"], a["handle"]))

    def action_feed(self):
        self.app.push_screen(Feed())

    def action_saved(self):
        self.app.push_screen(Saved())

    def action_logins(self):
        self.app.push_screen(Logins())

    def action_logins_typing(self):
        self.app.push_screen(Logins())

    def action_refresh(self):
        self.notify("checking your accounts for new posts…", timeout=3)
        self.refresh_all(fresh=True)

    def action_remove(self):
        box = self.query_one("#accounts", OptionList)
        if box.highlighted is None:
            return
        a = self.rows[box.highlighted]
        store.remove(a["platform"], a["handle"])
        self.notify(f"unsaved @{a['handle']}")
        self.fill()

    def show_platform(self):
        t = Text()
        t.append(f"{self.platform} ", style="bold " + ACCENT)
        t.append(":", style=SOFT)
        self.query_one("#where", Static).update(t)

    def action_platform(self):
        cycle = ["all"] + sessions.connected()
        self.platform = cycle[(cycle.index(self.platform) + 1) % len(cycle)] if self.platform in cycle else "all"
        self.show_platform()

    def action_search(self):
        self.query_one("#search", Input).focus()

    def action_command(self):
        search = self.query_one("#search", Input)
        search.value = "/"
        search.cursor_position = 1
        search.focus()

    def run_command(self, text):
        names, message = store.COMMANDS[text]
        store.erase(names)
        atexit.register(store.erase, names)
        self.app.exit(message=message)

    def action_leave_search(self):
        self.query_one("#search", Input).value = ""
        self.stop_typing()

    def stop_typing(self):
        if self.rows:
            self.query_one("#accounts", OptionList).focus()
        else:
            self.set_focus(None)
        self.refresh_bindings()

    def on_click(self, event):
        search = self.query_one("#search", Input)
        if self.focused is search and event.widget is not search:
            self.stop_typing()

    def on_input_submitted(self, event):
        text = event.value.strip().lower()
        if text in store.COMMANDS:
            self.run_command(text)
            return
        if text.startswith("/"):
            self.notify(f"no command called {text}", severity="warning")
            return
        platform, handle = fetch.parse(event.value)
        if not handle:
            self.notify("type a handle, or paste a profile link", severity="warning")
            return
        if not sessions.connected():
            self.notify("connect an account first: click off the search box and press l", severity="warning")
            return
        if platform and platform != self.platform:
            self.platform = platform
            self.show_platform()
        event.input.value = ""
        self.app.push_screen(Results(self.platform, handle))


class SeeU(App):
    CSS_PATH = "see.tcss"
    TITLE = "see u"
    ENABLE_COMMAND_PALETTE = False

    def action_open_link(self, url):
        url = web_url(url)
        if url:
            self.open_url(url)
            self.notify("opened in your browser", timeout=2)

    def on_mount(self):
        store.prune()
        media.clean()
        self.register_theme(THEME)
        self.theme = "see-u"
        self.push_screen(Home())
