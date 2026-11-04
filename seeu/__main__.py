import subprocess
import sys

REMOVED = "your accounts are gone from see u on this computer. saved posts were kept in ~/.seeu/saved"

args = sys.argv[1:]
if args == ["update"]:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp", "instaloader"])
    print("see u's fetchers are up to date")
elif args == ["/removeme"]:
    from .store import wipe

    wipe()
    print(REMOVED)
elif args:
    print("just type:  seeu")
else:
    from .app import SeeU

    SeeU().run()
