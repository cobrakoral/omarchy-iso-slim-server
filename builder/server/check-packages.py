#!/usr/bin/env python3
"""Refuse graphical services and runtime stacks in the resolved target closure."""

import re
import sys
from pathlib import Path

FORBIDDEN = re.compile(
    r"^(libnotify|libx11|libxcb|libxrender|libxft|cairo|pango|fontconfig|gdk-pixbuf2|networkmanager|hypr.*|quickshell|sddm|gdm|lightdm|xorg-.*|wayland|pipewire.*|wireplumber|pulseaudio.*|bluez.*|cups.*|docker.*|podman.*|cockpit.*|qt[56]-.*|gtk[234].*|ttf-.*|noto-fonts.*|cloud-init)$"
)


def check(names):
    bad = sorted(n for n in names if FORBIDDEN.fullmatch(n))
    if bad:
        raise ValueError("Unwanted target dependencies: " + ", ".join(bad))


if __name__ == "__main__":
    check(Path(sys.argv[1]).read_text().split())
