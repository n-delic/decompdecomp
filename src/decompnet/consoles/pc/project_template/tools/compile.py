"""Compile one PC unit and normalize its symbol names, without a shell parsing Windows paths or compiler flags."""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COMPILE_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# what wine is told: no debug output in the compiler's log, and no offer to install Mono or Gecko should it make the
# prefix (decompdecomp makes it when Visual C++ 2017 is installed: its wine.py)
WINE_QUIET = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="}


def windows_path(path: str) -> str:
    """A path as a Windows program under wine reads it: /home/me/x.h is Z:\\home\\me\\x.h (wine's Z: is /), and
    a relative one keeps its place with backslashes. cl.exe reads /home/... as a switch."""
    return ("Z:" if path.startswith("/") else "") + path.replace("/", "\\")


def keep_wine_server(wine: str, env: dict) -> None:
    """A wine server that stays (`wineserver -p`): wine started afresh for every file takes seconds. One already
    running stays, and the new one leaves at once. It's given no pipe of the build's: a server holding one would
    keep the build waiting for its output to end."""
    beside = [folder / "wineserver" for folder in (Path(wine).parent, Path(wine).resolve().parent)]
    server = next((str(s) for s in beside if s.is_file()), None) or shutil.which("wineserver")
    if server is None:
        return  # (wine starts one of its own, which only lives a few seconds)
    try:
        subprocess.run([server, "-p"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main() -> int:
    """Keep the compiler's arguments intact, and never rename symbols after a failed compile."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--wrapper", default="")  # wibo
    parser.add_argument("--wine", default="")  # wine, for Visual C++ 2015 and newer (configure.py's WINE_FROM)
    parser.add_argument("--wine-prefix", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    env, path = None, str
    if args.wine:
        env = {**os.environ, **WINE_QUIET, **({"WINEPREFIX": args.wine_prefix} if args.wine_prefix else {})}
        keep_wine_server(args.wine, env)
        path = windows_path
    command = ([args.wine] if args.wine else [args.wrapper] if args.wrapper else []) + [
        args.compiler, *flags, f"/I{path('include')}", f"/I{path(args.include)}", f"/Fo{path(args.output)}",
        path(args.source)]
    if args.wine:
        # wine's output to a file, then ours: the services the first wine program after a new server starts
        # (services.exe, winedevice.exe, rpcss.exe...) keep what it was given for as long as the server runs, and
        # given the build's pipe, they kept the build waiting for its end
        with tempfile.TemporaryFile() as said:
            try:
                result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL, stdout=said, stderr=said,
                                        timeout=COMPILE_SECONDS, creationflags=NO_WINDOW)
            finally:
                said.seek(0)
                text = said.read().decode("utf-8", "replace")
                for name in (args.source, args.include, args.output):  # (its messages name them as given to it)
                    text = text.replace(windows_path(name), name)
                sys.stdout.write(text)
                sys.stdout.flush()
    else:
        result = subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW)
    if result.returncode:
        return result.returncode
    return subprocess.run([sys.executable, str(Path(__file__).with_name("names.py")), args.config, args.output],
                          timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode


if __name__ == "__main__":
    sys.exit(main())
