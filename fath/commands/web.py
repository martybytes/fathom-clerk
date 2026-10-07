"""`fath web` -- the browser dashboard."""

from __future__ import annotations

import errno
import subprocess
import sys
import urllib.error
import urllib.request

from fath import config, paths
from fath.server import Server

HELP = """usage: fath web [--port n] [--no-browser]

Starts a local dashboard on 127.0.0.1 and opens it. Bound to loopback only, so
nothing on the network can reach it.

options:
  --port <n>      override the saved port
  --no-browser    do not open a browser
  -h, --help      this help
"""


def _already_healthy(port: int) -> bool:
    """Is something on this port already us?

    Answering this before reporting a bind failure is the difference between
    "it is already running, here is the link" and an unexplained error.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.5) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _open_browser(url: str) -> None:
    # Not webbrowser.open: its fallbacks shell out to console programs. This is a
    # fixed loopback URL, so handing it to the shell is safe.
    try:
        if paths.is_windows():
            subprocess.run(["cmd.exe", "/c", "start", "", url], check=False, timeout=15)
        elif sys.platform == "darwin":
            subprocess.run(["open", url], check=False, timeout=15)
        else:
            subprocess.run(["xdg-open", url], check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass  # printing the URL is enough; failing to launch a browser is not fatal


def main(argv: list[str]) -> int:
    conf = config.shared()
    port = conf.get_int("web.port", 8899)
    open_browser = conf.get_bool("web.openBrowser", True)

    index = 0
    while index < len(argv):
        item = argv[index]
        if item in ("-h", "--help"):
            print(HELP)
            return 0
        if item == "--port" and index + 1 < len(argv):
            try:
                port = int(argv[index + 1])
            except ValueError:
                print("fath web: --port needs a number", file=sys.stderr)
                return 2
            index += 1
        elif item == "--no-browser":
            open_browser = False
        else:
            print(f"fath web: unknown option '{item}' (try -h)", file=sys.stderr)
            return 2
        index += 1

    if _already_healthy(port):
        url = f"http://127.0.0.1:{port}/"
        print(f"already running at {url}")
        if open_browser:
            _open_browser(url)
        return 0

    server = Server(conf, port)

    def ready(url: str) -> None:
        print(f"fathom-helper dashboard: {url}")
        print("  bound to 127.0.0.1 only. Ctrl-C to stop.")
        if open_browser:
            _open_browser(url)

    try:
        server.serve_forever(ready)
    except KeyboardInterrupt:
        print("\nstopping...")
        server.shutdown()
        return 0
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 10048):
            print(
                f"fath web: port {port} is taken by something that is not fathom-helper",
                file=sys.stderr,
            )
            print(f"  try: fath web --port {port + 1}", file=sys.stderr)
            return 1
        raise
    return 0
