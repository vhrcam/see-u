#!/bin/bash
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "see u needs Python 3.10 or newer. Get it from python.org, then run this again."; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "see u needs Python 3.10 or newer (this is $(python3 -V)). Get it from python.org, then run this again."; exit 1; }
echo "setting up see u…"
python3 -m venv .venv
.venv/bin/pip install -q --disable-pip-version-check -r requirements.txt
mkdir -p ~/.local/bin
ln -sf "$PWD/see" ~/.local/bin/seeu
echo "done. type seeu to start."
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *)
    rc=~/.zshrc
    [ "${SHELL##*/}" = bash ] && rc=~/.bashrc
    echo
    echo "~/.local/bin isn't on your PATH yet, so seeu won't be found. Run this, then open a new terminal:"
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $rc"
    ;;
esac
if ! command -v mpv >/dev/null; then
  echo
  echo "for the player, install mpv: brew install mpv on a Mac, or sudo apt install mpv on Ubuntu/Debian."
fi
