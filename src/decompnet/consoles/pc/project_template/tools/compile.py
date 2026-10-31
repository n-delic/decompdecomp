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
# how long the wine server stays after its last compile: then it leaves by itself, however the app ended (quit, Ctrl-C,
# killed); the next compile starts one again. Only here: this file is copied into each project and runs without
# decompdecomp (standard library only), so it can't share a constant with the app's wine.py
SERVER_IDLE_SECONDS = 600


def windows_path(path: str) -> str:
    """A path as a Windows program under wine reads it: /home/me/x.h is Z:\\home\\me\\x.h (wine's Z: is /), and
    a relative one keeps its place with backslashes. cl.exe reads /home/... as a switch."""
    return ("Z:" if path.startswith("/") else "") + path.replace("/", "\\")


def keep_wine_server(wine: str, env: dict) -> None:
    """A wine server that stays between compiles (`wineserver -p<seconds>`): wine started afresh for every file takes
    seconds. One already running stays, and the new one leaves at once. It's given no pipe of the build's: a server
    holding one would keep the build waiting for its output to end."""
    beside = [folder / "wineserver" for folder in (Path(wine).parent, Path(wine).resolve().parent)]
    server = next((str(s) for s in beside if s.is_file()), None) or shutil.which("wineserver")
    if server is None:
        return  # (wine starts one of its own, which only lives a few seconds)
    try:
        subprocess.run([server, f"-p{SERVER_IDLE_SECONDS}"], env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def wine_setup() -> tuple[str, dict]:
    """The wine to run and its environment, found when the file builds (so a project configured before Visual C++
    2017 or wine was installed builds a 2017 file as soon as they are): $DECOMPNET_WINE, else wine on the PATH, in
    the prefix $DECOMPNET_WINE_PREFIX names, decompdecomp's own (it sets both for its builds). Never ~/.wine."""
    wine = os.environ.get("DECOMPNET_WINE") or shutil.which("wine")
    if not wine:
        sys.exit("compile.py: Visual C++ 2015 and newer, and the linker of link-time code generation (/GL, /LTCG), run "
                 "through wine on Linux and macOS, and this computer has no "
                 "wine: decompdecomp's Compilers & tools says how to get it")
    prefix = os.environ.get("DECOMPNET_WINE_PREFIX")
    if not prefix:
        sys.exit("compile.py: DECOMPNET_WINE_PREFIX names no wine prefix: decompdecomp sets it for its builds; by "
                 "hand, point it at a folder of its own (never your ~/.wine)")
    return wine, {**os.environ, **WINE_QUIET, "WINEPREFIX": prefix}


def main() -> int:
    """Keep the compiler's arguments intact, and never rename symbols after a failed compile."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", required=True, action="append")  # each searched in turn: Visual C++'s, an SDK's
    parser.add_argument("--config", required=True)
    parser.add_argument("--wrapper", default="")  # wibo
    parser.add_argument("--wine", action="store_true")  # through wine: Visual C++ 2015+ (configure.py's WINE_FROM)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    env, path, wine = None, str, ""
    if args.wine:
        wine, env = wine_setup()
        keep_wine_server(wine, env)
        path = windows_path
    command = ([wine] if wine else [args.wrapper] if args.wrapper else []) + [
        args.compiler, *flags, f"/I{path('include')}", *(f"/I{path(d)}" for d in args.include),
        f"/Fo{path(args.output)}", path(args.source)]
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
                for name in (args.source, *args.include, args.output):  # (its messages name them as given to it)
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
