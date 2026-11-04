# see u

A terminal app for browsing TikTok, Instagram, and Snapchat with your own accounts, without the apps or their feeds. Cyan on black, with an eye at the top.

## Sign in

Press `l` for the logins menu. Pick a platform and a browser window opens on that platform's real sign-in page. Sign in the way you normally would, including 2FA, and the window closes by itself when you're in.

- A bright light means the account is connected. A dim blue light means it's signed out.
- Signed-out accounts stay on the list, so getting back in is one press of enter.
- You can connect several accounts on a platform and pick which one is in use.
- `x` signs out and deletes that account's saved session. `-` also takes it off the list.

see u never sees your password. It keeps the signed-in session in `~/.seeu/sessions`, readable only by your user. Signing in needs Chrome, Brave, Edge, or Chromium installed.

Connect a platform before browsing it. You can only see what that account is allowed to see, and see u has no way to like, follow, comment, or message.

## What it does

- **Look up accounts.** Press `:`, type a handle, and press enter. It searches your connected platforms together, and Tab switches between all and each one. Pasting a profile link picks the platform for you.
- **Closest matches.** Each platform gets its own list: the exact handle if it exists, then up to 10 of the closest real accounts.
- **Account screen.** Bio, exact follower and post counts, bio links, and the latest posts. `m` loads every older post, all the way back, and shows its progress. Press `m` again to pause.
- **Saved accounts.** Press `+` on an account to save it (and `-` to unsave). Saved accounts show on the home screen with their new-post counts, and the right side shows the newest posts from whichever one is highlighted.
- **Feed.** Every saved account in one stream, newest first. New posts sit on top, then a "you're caught up" line.
- **Preview pane** with a cyan thumbnail, the date, views, likes, comments, and the full caption.
- **Open the real thing.** Enter opens the photo, album, video, or story in an mpv window: full color, with sound. Nothing moves on by itself. In a story, highlight, or album, `a`/`d` (or a click) go to the previous or next item. In a single video, `a`/`d` skip 3 seconds. Space pauses, and `q` or esc closes. Without mpv, see u falls back to your computer's normal photo and video apps.
- **Filter** with `/` by caption, handle, or post type.
- **Save** with `v`. The post and its media go into a folder you pick, and the saved screen works offline.
- **Copy a link** with `c`.

## Good to know

- see u isn't affiliated with TikTok, Instagram, or Snapchat, and it reads their websites the way a browser does. Their terms may not allow that, and a platform can limit or restrict an account it thinks is automated. Use it at your own risk, and go easy on bulk loading.
- Your saved accounts, sessions, and posts live in `~/.seeu`, outside the repo.

## Setup

Mac or Linux:

```
git clone https://github.com/vhrcam/see-u.git
cd see-u
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p ~/.local/bin
ln -s "$PWD/see" ~/.local/bin/seeu
```

Then type `seeu` from anywhere. For the media player, install mpv: `brew install mpv` on a Mac, or `sudo apt install mpv` on Ubuntu/Debian.

Windows (in PowerShell, from the see-u folder):

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
winget install mpv
```

Then run `seeu.cmd`. Windows Terminal shows the colors best. Windows and Linux aren't tested yet.

## Keys

Enter opens, esc goes back, and `w`/`s` work like the up and down arrows. With the mouse, click once to highlight and again to open.

| Where | Keys |
|---|---|
| Home | `:` search, `tab` platform, `l` logins, `f` feed, `v` saved posts, `r` refresh, `x` unsave, `q` quit (`ctrl+q` while typing) |
| Logins | enter connect, reconnect, or switch; `x` sign out; `-` forget |
| Posts | `/` filter, `v` save, `c` copy link |
| Account | `+` save account, `-` unsave, `m` load every older post (again to pause), `r` refresh |
| Saved | `x` delete |
| Player | `a`/`d` previous/next item (or skip 3 seconds in a single video), click for next, space pause, `q` close |

## Keeping it working

These sites change often. If something stops loading, run `seeu update` to update the two fetchers (yt-dlp and instaloader).

Built with Python, Textual, yt-dlp, instaloader, and Pillow.
