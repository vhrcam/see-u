import shutil

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from . import media
from .browse import Browser
from .widgets import ACCENT, DIM, SOFT, TOP, FilterBar, Nav, Posts, Preview


class FolderPick(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def compose(self):
        with Vertical(id="folderbox"):
            yield Static(Text("save to a folder", style="bold " + TOP))
            yield Input(placeholder="new folder name, or enter for 'saved'", id="folder")
            yield Nav(*[Option(Text(f"  {f}", style=ACCENT), id=f) for f in media.folders()], id="folders")
            yield Static(Text("enter saves · ↓ picks an existing folder · esc cancels", style=SOFT))

    def on_mount(self):
        self.query_one("#folders", OptionList).display = bool(media.folders())
        self.query_one("#folder", Input).focus()

    def on_input_submitted(self, event):
        self.dismiss(media.folder_name(event.value))

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)

    def on_key(self, event):
        box = self.query_one("#folders", OptionList)
        if event.key == "down" and self.focused is self.query_one("#folder", Input) and box.display:
            box.focus()
            event.stop()

    def action_cancel(self):
        self.dismiss(None)


class Saved(Browser):
    BINDINGS = [
        Binding("x", "delete", "delete"),
    ]

    def __init__(self):
        super().__init__()
        self.items = []

    def compose(self):
        yield Static(id="savedhead")
        with Horizontal(id="body"):
            yield Posts(id="posts")
            yield Preview(id="preview")
        yield FilterBar()
        yield Footer()

    def on_mount(self):
        self.items = media.saved()
        self.redraw()
        self.query_one(Posts).focus()

    def check_action(self, action, parameters):
        if action == "save":
            return False
        return super().check_action(action, parameters)

    def head(self, shown):
        t = Text()
        t.append("saved posts", style="bold " + TOP)
        groups = {}
        for p in self.items:
            groups[p["folder"]] = groups.get(p["folder"], 0) + 1
        t.append(f"   {len(self.items)} posts", style=DIM)
        if groups:
            t.append("   " + "  ·  ".join(f"{k} {v}" for k, v in sorted(groups.items())), style=SOFT)
        if self.words:
            t.append(f"   {len(shown)} match", style=ACCENT)
        if not self.items:
            t.append("\nnothing saved yet. press v on any post to keep it here, offline", style=SOFT)
        return t

    def redraw(self):
        shown = [p for p in self.items if self.matches(p)]
        self.query_one("#savedhead", Static).update(self.head(shown))
        box = self.query_one(Posts)
        keep = box.current()
        box.set_posts(shown, who=True, footer="no posts match" if self.words and not shown else None)
        if keep:
            box.select_id(keep["id"])

    def thumb_for(self, p, cols):
        return media.saved_thumb(p, cols, 15)

    def view(self, p):
        files = media.saved_files(p)
        if not files:
            raise FileNotFoundError(p["saved_dir"])
        media.play(files)

    def action_delete(self):
        p = self.query_one(Posts).current()
        if not p:
            return
        shutil.rmtree(p["saved_dir"], ignore_errors=True)
        self.items = media.saved()
        self.notify(f"deleted from {p['folder']}")
        self.redraw()
