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
    parser.add_argument("--family", choices=("msvc", "watcom"), default="msvc")
    parser.add_argument("--watcom-root", default="", help="the original Watcom installation; BINNT loads BINW or BINB")
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
    if args.family == "watcom":
        result = compile_watcom(args, flags)
        if result:
            return result
        return subprocess.run([sys.executable, str(Path(__file__).with_name("names.py")), args.config, args.output],
                              timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode
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


def compile_watcom(args, flags: list[str]) -> int:
    """#81: run the Windows loader against the user's compiler body, then convert before names.py or the judge.

    Watcom treats an absolute POSIX source as options and splits long filenames, even when quoted. Short aliases
    read source and headers in place. Its OMF output stays temporary, so failure never replaces a usable object.
    The conversion module is copied from arch/x86_omf.py when the project tools are written.
    """
    import hashlib
    import json

    import linear
    import names
    from omf import OMFError, convert
    from segments import Resolver

    root = Path(args.watcom_root)
    compiler = str(Path(args.compiler).resolve())  # the child runs in a temporary directory, not the project
    try:
        folders = {p.name.lower(): p for p in root.iterdir() if p.is_dir()} if args.watcom_root else {}
        body = next((folders[n] for n in ("binw", "binb") if n in folders
                     and any(p.name.lower() == Path(args.compiler).name.lower() for p in folders[n].iterdir())), None)
    except OSError:
        body = None
    if body is None or args.wine:
        print("compile.py: Watcom needs --watcom-root with its BINW or BINB compiler body, and a native Windows or wibo runner.",
              file=sys.stderr)
        return 1
    if sys.platform != "win32" and not args.wrapper:
        print("compile.py: Watcom on Linux and macOS needs the pinned wibo runner (--wrapper).", file=sys.stderr)
        return 1
    support = list(dict.fromkeys([body, *(folders[n] for n in ("bin", "binw") if n in folders)]))
    env = {**os.environ, "WATCOM": str(root.resolve()),
           "PATH": os.pathsep.join([*(str(p.resolve()) for p in support), os.environ.get("PATH", "")])}
    # A user's global WPP386/WCC386 options must not silently override this project's compiler flags.
    for name in tuple(env):
        if name.upper() in ("WCC386", "WPP386", "INCLUDE"):
            del env[name]
    with tempfile.TemporaryDirectory(prefix="watcom-") as temporary:
        stage = Path(temporary)
        output = stage / "unit.obj"
        source = Path(args.source).resolve()
        includes = [source.parent, Path("include").resolve(), *(Path(d).resolve() for d in args.include)]
        # 10.5 splits quoted long filenames too, even in response files. Short aliases let it read the real source
        # and headers in place. No kit file is copied. Its temporary working directory needn't be on the same disk.
        if sys.platform != "win32":
            unit = stage / ("unit" + source.suffix)
            unit.symlink_to(source)
            folders = []
            for index, include in enumerate(includes):
                alias = stage / f"include{index}"
                alias.symlink_to(include, target_is_directory=True)
                folders.append(alias.name)
            source_name = unit.name
        else:
            # Windows without symlink privileges: ask for the existing files' short paths. Some volumes disable
            # these; fail with an actionable error rather than silently misparsing a user's folder name.
            import ctypes

            get_short = ctypes.windll.kernel32.GetShortPathNameW
            get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
            get_short.restype = ctypes.c_uint32

            def short_path(value):
                buffer = ctypes.create_unicode_buffer(32768)
                length = get_short(str(value), buffer, len(buffer))
                if not length or length >= len(buffer) or any(c.isspace() for c in buffer.value):
                    raise ValueError("Watcom 10.5 needs short paths: use source and compiler headers on a volume with 8.3 names enabled.")
                return buffer.value

            try:
                folders, source_name = [short_path(p) for p in includes], short_path(source)
            except ValueError as error:
                print(f"compile.py: {error}", file=sys.stderr)
                return 1
        command = ([args.wrapper] if args.wrapper else []) + [compiler, *flags,
                   *(f"-i={d}" for d in folders), "-fo=unit.obj", source_name]
        result = subprocess.run(command, cwd=stage, env=env, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW)
        if result.returncode:
            return result.returncode
        try:
            info = json.loads(Path(args.config).read_text(encoding="utf-8"))
            original = b""
            if info.get("exe"):
                with Path(info["exe"]).open("rb") as stream:
                    head = stream.read(0x10000)
                    if linear.header(head) is not None:
                        stream.seek(0)
                        original = stream.read(64 * 1024 * 1024 + 1)
                        if len(original) > 64 * 1024 * 1024:
                            raise ValueError("The LE executable exceeds the 64 MiB compiler-context limit.")
            context = None
            if linear.header(original) is not None:
                if hashlib.sha1(original).hexdigest() != info["sha1"]:
                    raise ValueError("The original LE executable changed; restore this project's own copy.")
                context = Resolver(info, linear.Linear(original), names)
            converted = convert(output.read_bytes(), segmented=context)
        except (OSError, OMFError, ValueError) as error:
            print(f"compile.py: Watcom output could not be converted: {error}", file=sys.stderr)
            return 1
        # Keep replacement on the destination filesystem; tools/compile.py can also be run outside ninja.
        with tempfile.NamedTemporaryFile(dir=Path(args.output).parent, delete=False) as written:
            pending = Path(written.name)
            written.write(converted)
        try:
            os.replace(pending, args.output)
        finally:
            pending.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
