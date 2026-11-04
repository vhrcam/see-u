import subprocess
import sys

from . import store

args = sys.argv[1:]
if args == ["update"]:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp", "instaloader"])
    print("see u's fetchers are up to date")
elif len(args) == 1 and args[0].lower() in store.COMMANDS:
    names, message = store.COMMANDS[args[0].lower()]
    store.erase(names)
    print(message)
elif args:
    print("just type:  seeu")
else:
    from .app import SeeU

    SeeU().run()
