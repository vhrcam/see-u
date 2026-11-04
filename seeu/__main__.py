import subprocess
import sys

args = sys.argv[1:]
if args == ["update"]:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp", "instaloader"])
    print("see u's fetchers are up to date")
elif args and args != ["u"]:
    print("just type:  seeu")
else:
    from .app import SeeU

    SeeU().run()
