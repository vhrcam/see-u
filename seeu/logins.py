import shutil

from rich.text import Text
from textual import work
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from . import sessions, signin
from .widgets import ACCENT, DIM, SOFT, WHITE, Nav

LIGHT_ON = "#7df9ff"
LIGHT_OFF = "#1d4e89"


def light(on):
    return Text("●", style=("bold " + LIGHT_ON) if on else LIGHT_OFF)


def lights():
    t = Text()
    on = sessions.connected()
    for platform in sessions.PLATFORMS:
        t.append("● ", style=("bold " + LIGHT_ON) if platform in on else LIGHT_OFF)
        t.append(f"{platform}   ", style=ACCENT if platform in on else SOFT)
    return t


class Logins(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("x", "sign_out", "sign out"),
        Binding("minus", "forget", "forget", key_display="-"),
    ]

    def __init__(self):
        super().__init__()
        self.rows = []
        self.busy = None
        self.stop = False
        self.pulse = False

    def compose(self):
        yield Static(id="loginhead")
        yield Nav(id="logins")
        yield Footer()

    def on_mount(self):
        self.fill()
        self.query_one("#logins", OptionList).focus()
        self.set_interval(0.6, self.beat)
        self.recheck()

    @work(thread=True, exclusive=True, group="recheck")
    def recheck(self):
        if signin.recheck():
            self.app.call_from_thread(self.fill)

    def beat(self):
        if self.busy:
            self.pulse = not self.pulse
            self.fill()

    def is_busy(self, platform, user):
        return self.busy == (platform, user)

    def head(self):
        t = Text()
        t.append("logins\n", style="bold " + WHITE)
        if self.busy:
            t.append(f"finish signing in to {self.busy[0]} in the {signin.browser_name()} window. ", style=ACCENT)
            t.append("it closes by itself when you're in. esc cancels", style=DIM)
        else:
            t.append("your password stays in the browser window. see u only keeps the signed-in session, on this computer", style=DIM)
        return t

    def login_row(self, l):
        on = sessions.is_on(l)
        busy = self.is_busy(l["platform"], l["user"])
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append("  ")
        t.append_text(light(self.pulse if busy else on))
        t.append(f"  @{l['user'][:24]:<26}", style=WHITE if on else DIM)
        t.append(f"{l['platform']:<12}", style=ACCENT if on else DIM)
        if busy:
            t.append("waiting for you to sign in…", style=ACCENT)
        elif on and sessions.current(l["platform"]) == l:
            t.append("connected", style=ACCENT)
        elif on:
            t.append("connected · enter to use this one", style=DIM)
        else:
            t.append("signed out · enter to reconnect", style=DIM)
        return t

    def new_row(self, platform):
        busy = self.is_busy(platform, None)
        t = Text(no_wrap=True)
        t.append("  ")
        if busy:
            t.append_text(light(self.pulse))
        else:
            t.append("+", style=ACCENT)
        t.append(f"  {platform}", style=WHITE)
        if busy:
            t.append("   waiting for you to sign in…", style=ACCENT)
        return t

    def fill(self):
        self.rows = sessions.logins()
        box = self.query_one("#logins", OptionList)
        keep = box.highlighted
        options = [Option(self.login_row(l), id=f"l{i}") for i, l in enumerate(self.rows)]
        if options:
            options.append(Option(Text(""), disabled=True))
        options.append(Option(Text("  connect an account", style=DIM), disabled=True))
        options += [Option(self.new_row(p), id=f"n{p}") for p in sessions.PLATFORMS]
        box.clear_options()
        box.add_options(options)
        first = next(i for i, o in enumerate(options) if o.id)
        box.highlighted = keep if keep is not None and keep < len(options) and options[keep].id else first
        self.query_one("#loginhead", Static).update(self.head())
        self.refresh_bindings()

    def selected(self):
        box = self.query_one("#logins", OptionList)
        if box.highlighted is None:
            return None
        oid = box.get_option_at_index(box.highlighted).id or ""
        return self.rows[int(oid[1:])] if oid.startswith("l") else None

    def check_action(self, action, parameters):
        if action == "sign_out":
            l = self.selected()
            return bool(l and sessions.is_on(l)) and not self.busy
        if action == "forget":
            return bool(self.selected()) and not self.busy
        return True

    def on_option_list_option_highlighted(self, event):
        self.refresh_bindings()

    def on_option_list_option_selected(self, event):
        if self.busy:
            self.notify("finish the sign-in that's open first, or press esc to cancel", timeout=3)
            return
        oid = event.option.id or ""
        if oid.startswith("n"):
            self.begin(oid[1:], None)
            return
        l = self.rows[int(oid[1:])]
        if not sessions.is_on(l):
            self.begin(l["platform"], l["user"])
        elif sessions.current(l["platform"]) != l:
            sessions.use(l)
            self.notify(f"using @{l['user']} on {l['platform']}", timeout=3)
            self.fill()
        else:
            self.notify(f"@{l['user']} is connected", timeout=2)

    def begin(self, platform, user):
        if not signin.browser():
            self.notify("see u signs in through Chrome, Brave, Edge, or Chromium. install one and try again",
                        severity="error", timeout=8)
            return
        self.busy = (platform, user)
        self.stop = False
        self.fill()
        self.run_sign_in(platform, user)

    @work(thread=True, exclusive=True, group="signin")
    def run_sign_in(self, platform, user):
        profile = sessions.browser_profile(platform, user or "new")
        if not user:
            shutil.rmtree(profile, ignore_errors=True)
        try:
            who, cookies, agent = signin.sign_in(platform, profile, lambda: self.stop)
        except Exception as e:
            if not user:
                shutil.rmtree(profile, ignore_errors=True)
            note = {signin.Cancelled: "sign-in cancelled",
                    signin.TimedOut: "sign-in timed out, try again when you're ready"}.get(type(e), "couldn't finish signing in, try again")
            self.app.call_from_thread(self.finished, None, note)
        else:
            final = sessions.browser_profile(platform, who["user"])
            if final != profile:
                shutil.rmtree(final, ignore_errors=True)
                profile.replace(final)
            sessions.add(platform, who["user"], who.get("name"), cookies, agent)
            self.app.call_from_thread(self.finished, who["user"], None)

    def finished(self, user, note):
        platform = self.busy[0] if self.busy else None
        self.busy = None
        self.fill()
        if user:
            box = self.query_one("#logins", OptionList)
            box.highlighted = next(i for i, l in enumerate(self.rows) if (l["platform"], l["user"]) == (platform, user.lower()))
            self.notify(f"connected @{user} on {platform}", timeout=4)
        elif note:
            self.notify(note, timeout=4)

    def action_sign_out(self):
        l = self.selected()
        if l:
            sessions.sign_out(l)
            self.notify(f"signed out @{l['user']}. press enter to reconnect", timeout=4)
            self.fill()

    def action_forget(self):
        l = self.selected()
        if l:
            sessions.forget(l)
            self.notify(f"forgot @{l['user']}", timeout=3)
            self.fill()

    def action_back(self):
        if self.busy:
            self.stop = True
            return
        self.app.pop_screen()
