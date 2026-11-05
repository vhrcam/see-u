import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.text import Text
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from . import fetch, sessions, store
from .account import Account
from .widgets import ACCENT, DIM, SOFT, TEXT, TOP, Nav, human

PLATFORMS = ["all", "tiktok", "instagram", "snapchat"]
POPULAR = json.loads(Path(__file__).with_name("popular.json").read_text())
VALID = re.compile(r"[a-z0-9_.-]{2,30}")
CHECKS = {"tiktok": 16, "instagram": 4, "snapchat": 6}
_probed = {}


def distance(a, b):
    cost = lambda c: 0.5 if c in "._" else 1
    d = [[0.0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        d[i][0] = d[i - 1][0] + cost(a[i - 1])
    for j in range(1, len(b) + 1):
        d[0][j] = d[0][j - 1] + cost(b[j - 1])
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + cost(a[i - 1]), d[i][j - 1] + cost(b[j - 1]),
                          d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def score(q, handle, name=None):
    best = 0
    for target, weight in ((handle, 1.0), (re.sub(r"[^a-z0-9]", "", (name or "").lower()), 0.6)):
        if not target:
            continue
        d = distance(q, target)
        if d <= max(1, len(q) // 4):
            s = 1 / (1 + d)
        elif len(q) >= 3 and (q in target or target in q):
            s = 0.15 + 0.3 * min(len(q), len(target)) / max(len(q), len(target))
        else:
            s = 0
        best = max(best, s * weight)
    return best


def rank(platform, s, handle, prof=None):
    if handle in POPULAR.get(platform, {}):
        s += 0.35
    fans = (prof or {}).get("followers") or (prof or {}).get("subscribers")
    if fans:
        s += min(math.log10(fans + 1) / 20, 0.45)
    return s


def closest(platform, query, limit=10, skip=()):
    q = query.lower().lstrip("@")
    pool = dict(POPULAR.get(platform, {}))
    for a in store.accounts():
        if a["platform"] == platform:
            pool[a["handle"]] = a.get("name") or pool.get(a["handle"])
    scored = [(score(q, h, n), h, n) for h, n in pool.items() if h != q and h not in skip]
    scored = sorted((s for s in scored if s[0] > 0), key=lambda s: -rank(platform, s[0], s[1], profile(platform, s[1])))
    return [(h, n) for s, h, n in scored][:limit]


def profile(platform, handle):
    return (fetch.cached(platform, handle) or {}).get("profile")


def typos(h):
    ranked = []

    def add(x, rank):
        if x != h and VALID.fullmatch(x):
            ranked.append((rank, x))

    n = len(h)
    add(re.sub(r"(.)\1+", r"\1", h), 0)
    add(h.replace("_", ""), 0)
    add(h.replace(".", ""), 0)
    add(h.replace(".", "_"), 1)
    add(h.replace("_", "."), 1)
    for i in range(n):
        add(h[:i] + h[i + 1:], 1 + (n - 1 - i) * 0.5)
    for i in range(n - 1):
        add(h[:i] + h[i + 1] + h[i] + h[i + 2:], 1.2 + (n - 2 - i) * 0.5)
    for i in range(n):
        add(h[:i + 1] + h[i] + h[i + 1:], 2 + (n - 1 - i) * 0.5)
    for a in ("official", "_official", "_", "tv", "hq"):
        add(h + a, 3)
    for a in ("the", "real", "its", "_"):
        add(a + h, 3.5)
    out = []
    for _, x in sorted(ranked):
        if x not in out:
            out.append(x)
    return out


def match_line(handle, name=None, prof=None, added=False, note=None):
    t = Text(no_wrap=True)
    if prof:
        t.append(f"  {(prof.get('name') or handle)[:26]}", style="bold " + TOP)
        if prof.get("verified"):
            t.append(" ✓", style=ACCENT)
        t.append(f"   @{handle}", style=ACCENT)
        stats = [f"{human(prof[k])} {k}" for k in ("followers", "posts") if prof.get(k) is not None]
        if prof.get("context"):
            stats.append(prof["context"])
        if stats:
            t.append("   " + " · ".join(stats), style=DIM)
    else:
        t.append(f"  @{handle[:28]:<30}", style=ACCENT)
        t.append((name or "")[:30], style=TEXT)
    if added:
        t.append("   saved", style=SOFT)
    if note:
        t.append(f"   {note}", style="bold " + TOP)
    return t


class Results(Screen):
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("tab", "platform", "platform", priority=True),
    ]

    def __init__(self, platform, query, place=""):
        super().__init__()
        self.platform = platform
        self.q = query.lower()
        self.place = " ".join(place.lower().split())
        self.token = 0
        self.state = {}
        self.targets = []

    def compose(self):
        yield Static(id="searching")
        yield Nav(id="matches")
        yield Footer()

    def on_mount(self):
        self.run()

    def run(self):
        self.token += 1
        head = Text()
        head.append(f"searching {self.platform} for ", style=DIM)
        head.append(f"@{self.q}", style="bold " + TOP)
        if self.place:
            head.append("  ·  posts from ", style=DIM)
            head.append(self.place, style="bold " + TOP)
        self.query_one("#searching", Static).update(head)
        platforms = (sessions.connected() or PLATFORMS[1:]) if self.platform == "all" else [self.platform]
        self.state = {p: {"exact": None, "note": None, "found": [], "checking": True, "hits": {},
                          "placing": bool(self.place), "tags": None} for p in platforms}
        self.show()
        for p in platforms:
            self.run_worker(lambda p=p, t=self.token: self.search(p, t), thread=True, group=f"search-{p}", exclusive=True)

    def real_results(self, platform):
        if platform != "instagram":
            return None
        key = ("real", platform, self.q)
        if key in _probed and time.time() - _probed[key][0] < fetch.TTL:
            return _probed[key][1]
        try:
            users = fetch.instagram_search(self.q)
        except Exception:
            return None
        _probed[key] = (time.time(), users)
        return users

    def search(self, platform, token):
        self.find(platform, token)
        if token == self.token and self.place:
            self.check_places(platform, token)

    def check_places(self, platform, token):
        st = self.state.get(platform)
        if not st or st.get("off"):
            self.app.call_from_thread(self.update, platform, token, placing=False)
            return
        exact = st["exact"]["handle"] if st["exact"] else None
        handles = ([exact] if exact else []) + [h for h, _, _ in self.near(platform, st, exact or self.q)]
        hits = {}

        def count(h):
            if token != self.token:
                return h, None
            try:
                posts = fetch.account(platform, h)["posts"]
            except Exception:
                return h, None
            if platform == "instagram":
                time.sleep(1)
            return h, sum(fetch.from_place(p, self.place) for p in posts)

        with ThreadPoolExecutor(1 if platform == "instagram" else 4) as pool:
            for f in as_completed([pool.submit(count, h) for h in handles]):
                h, n = f.result()
                hits[h] = n
                self.app.call_from_thread(self.update, platform, token, hits=dict(hits))
        if exact and token == self.token:
            try:
                posts = fetch.account(platform, exact)["posts"]

                def step(done, total):
                    hits[exact] = sum(fetch.from_place(p, self.place) for p in posts)
                    self.app.call_from_thread(self.update, platform, token, hits=dict(hits), tags=(done, total))

                fetch.read_places(posts, step, lambda: token != self.token)
            except Exception:
                pass
        self.app.call_from_thread(self.update, platform, token, placing=False, tags=None)

    def find(self, platform, token):
        if not sessions.current(platform):
            self.app.call_from_thread(self.update, platform, token, exact=False, off=True, checking=False,
                                      note=f"connect {platform} first: esc, then l on the home screen")
            return
        real = self.real_results(platform)
        if real is not None:
            self.app.call_from_thread(self.update, platform, token, real=True,
                                      found=[(u["handle"], u["name"], u) for u in real])
        top = next((u for u in real or [] if u["handle"] == self.q), None)
        try:
            prof, note = top or fetch.account(platform, self.q)["profile"], None
        except fetch.NotFound:
            prof, note = False, f"no account called @{self.q}"
        except fetch.Unavailable as e:
            prof, note = False, str(e)
        except Exception:
            prof, note = False, "couldn't check that handle right now"
        self.app.call_from_thread(self.update, platform, token, exact=prof, note=note)
        if real is not None:
            self.app.call_from_thread(self.update, platform, token, checking=False)
            return
        key = (platform, self.q)
        if key in _probed and time.time() - _probed[key][0] < fetch.TTL:
            self.app.call_from_thread(self.update, platform, token, found=_probed[key][1], checking=False)
            return
        hits = []

        def hit(handle, name, prof=None):
            hits.append((handle, name, prof))
            self.app.call_from_thread(self.update, platform, token, found=list(hits))

        shown = {h for h, _ in closest(platform, self.q)}
        famous = []
        if not POPULAR.get(platform):
            for other in ("instagram", "tiktok"):
                famous += [h for h, _ in closest(other, self.q, limit=3)]
        candidates = [c for c in dict.fromkeys(famous + typos(self.q)) if c not in shown and c != self.q]
        candidates = candidates[: CHECKS[platform]]
        if platform == "tiktok":
            with ThreadPoolExecutor(4) as pool:
                futures = {pool.submit(fetch.tiktok_name, c): c for c in candidates}
                for f in as_completed(futures):
                    if token == self.token and f.result():
                        hit(futures[f], f.result())
        else:
            for c in candidates:
                if token != self.token:
                    break
                try:
                    p = fetch.account(platform, c)["profile"]
                    hit(p["handle"], p["name"], p)
                except fetch.NotFound:
                    continue
                except Exception:
                    break
        if token == self.token:
            _probed[key] = (time.time(), hits)
        self.app.call_from_thread(self.update, platform, token, checking=False)

    def update(self, platform, token, **changes):
        if token == self.token and platform in self.state and self.is_attached:
            self.state[platform].update(changes)
            self.show()

    def on_unmount(self):
        self.token += 1

    def near(self, platform, st, exact):
        if st.get("real"):
            return [x for x in st["found"] if x[0] != exact][:10]
        pool = {h: (n, profile(platform, h)) for h, n in closest(platform, self.q, limit=20, skip={exact})}
        for h, n, p in st["found"]:
            if h != exact:
                pool[h] = (n, p)
        return sorted(((h, n, p) for h, (n, p) in pool.items()),
                      key=lambda x: -rank(platform, score(self.q, x[0], x[1]), x[0], x[2]))[:10]

    def place_note(self, n):
        return f"{n} post{'' if n == 1 else 's'} from {self.place}" if n else None

    def target(self, platform, handle, prompt):
        self.targets.append((platform, handle))
        return Option(prompt, id=f"t{len(self.targets) - 1}")

    def show(self):
        box = self.query_one("#matches", OptionList)
        keep = None
        if box.highlighted is not None and not all(box.get_option_at_index(i).disabled for i in range(box.highlighted)):
            o = box.get_option_at_index(box.highlighted)
            if o.id:
                keep = self.targets[int(o.id[1:])]
        self.targets = []
        options = []
        for platform, st in self.state.items():
            if len(self.state) > 1:
                options.append(Option(Text(f" {platform}", style="bold " + ACCENT), disabled=True))
            prof = st["exact"]
            exact = prof["handle"] if prof else self.q
            hits = st["hits"]
            matched = {h for h, n in hits.items() if n}
            narrow = bool(self.place and not st["placing"] and matched)
            top = prof and (not narrow or exact in matched)
            if top:
                options.append(self.target(platform, exact, match_line(exact, prof=prof, added=store.is_added(platform, exact),
                                                                       note=self.place_note(hits.get(exact)))))
            elif prof is None:
                options.append(self.target(platform, exact, match_line(exact, "looking…", added=store.is_added(platform, exact))))
            elif not prof:
                options.append(Option(Text(f"  {st['note']}", style=SOFT), disabled=True))
            if st.get("off"):
                options.append(Option(Text(""), disabled=True))
                continue
            near = self.near(platform, st, exact)
            if st.get("real"):
                title = "  more results" if prof else "  results"
            else:
                title = "  not it? closest" if prof else "  closest"
            if narrow:
                near = [x for x in near if x[0] in matched]
                title = f"  {'more accounts' if top else 'accounts'} with posts from {self.place}"
            if near:
                options.append(Option(Text(title, style=DIM), disabled=True))
                for h, n, p in near:
                    options.append(self.target(platform, h, match_line(h, n, prof=p, added=store.is_added(platform, h),
                                                                       note=self.place_note(hits.get(h)))))
            if st["checking"]:
                options.append(Option(Text("  checking close spellings…", style=SOFT), disabled=True))
            elif self.place and st["placing"]:
                tags = st["tags"]
                wait = (f"  reading the place tags on @{exact}'s posts… {tags[0]} of {tags[1]}" if tags
                        else f"  checking posts for {self.place}…")
                options.append(Option(Text(wait, style=SOFT), disabled=True))
            elif self.place and not matched:
                options.append(Option(Text(f"  no posts from {self.place} in these accounts, so here's every match", style=SOFT),
                                      disabled=True))
            elif not near and not narrow:
                options.append(Option(Text("  no close matches", style=SOFT), disabled=True))
            options.append(Option(Text(""), disabled=True))
        box.clear_options()
        box.add_options(options)
        pick = None
        for i, o in enumerate(options):
            if o.id and (keep is None or self.targets[int(o.id[1:])] == keep):
                pick = i
                break
        if pick is None:
            pick = next((i for i, o in enumerate(options) if o.id), None)
        if pick is not None:
            box.highlighted = pick
        box.focus()

    def on_option_list_option_selected(self, event):
        if event.option.id:
            platform, handle = self.targets[int(event.option.id[1:])]
            self.app.push_screen(Account(platform, handle, self.place))

    def action_platform(self):
        cycle = ["all"] + (sessions.connected() or PLATFORMS[1:])
        self.platform = cycle[(cycle.index(self.platform) + 1) % len(cycle)] if self.platform in cycle else "all"
        self.run()

    def action_back(self):
        self.app.pop_screen()
