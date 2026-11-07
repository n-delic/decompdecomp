"""Sandboxed compilation of untrusted agent-submitted C.

Agents never get to run their own compiler for anything that counts: every score and
every match is produced here, on the coordinator, with the project's pinned toolchain.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from decompnet.hostenv import NO_WINDOW, stop_process_tree

from . import compile_sessions, compile_timing, limits, linuxvm, wsl
from .request_timing import span

MAX_SOURCE_BYTES = 64 * 1024
# Per-compile resource caps (address space, CPU seconds, output file size) and a global
# concurrency cap, so a hostile submission can't take the host down with it.
LIMITS = ["--as=2147483648", "--cpu=30", "--fsize=67108864"]
def concurrency_limit():
    default = max(2, (os.cpu_count() or 4) - 1)
    try:
        requested = int(os.environ.get('DECOMP_COMPILER_THREADS', '0'))
    except ValueError:
        requested = 0
    return max(1, min(256, requested)) if requested > 0 else default


_COMPILER_THREADS = concurrency_limit()
_SLOTS = threading.BoundedSemaphore(_COMPILER_THREADS)
# Background jobs cannot occupy the last compiler slot needed by an interactive request.
_BACKGROUND_SLOTS = threading.BoundedSemaphore(max(1, _COMPILER_THREADS - 1))
_SCHEDULING = threading.local()
_ARTIFACT_SINK = contextvars.ContextVar("compiler_artifact_sink", default=None)
_CANCEL_CHECK = contextvars.ContextVar("compiler_cancel_check", default=None)


def background_thread():
    _SCHEDULING.background = True


@contextmanager
def _slot():
    acquired = []
    queued = time.monotonic()
    gates = ([_BACKGROUND_SLOTS] if getattr(_SCHEDULING, 'background', False) else []) + [_SLOTS]
    try:
        for gate in gates:
            while not gate.acquire(timeout=0.1):
                if check := _CANCEL_CHECK.get():
                    check()
            acquired.append(gate)
        if check := _CANCEL_CHECK.get():
            check()
        compile_timing.add('queue', time.monotonic() - queued)
        yield
    finally:
        for gate in reversed(acquired):
            gate.release()


@contextmanager
def cancellable(check):
    """Make compiler subprocesses stop promptly when their owning background job is cancelled."""
    token = _CANCEL_CHECK.set(check)
    try:
        yield
    finally:
        _CANCEL_CHECK.reset(token)


@contextmanager
def capture_artifacts(sink):
    """Opt-in diagnostic reader, invoked before the private compile directory is removed."""
    token = _ARTIFACT_SINK.set(sink)
    try:
        yield
    finally:
        _ARTIFACT_SINK.reset(token)

_COMMENTS_AND_STRINGS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_FORBIDDEN = [
    (re.compile(r"\b(__)?asm(__)?\b"), "inline assembly is not allowed: matches must come from C"),
    (re.compile(r"\b(GLOBAL_ASM|INCLUDE_ASM|INCLUDE_RODATA|NON_MATCHING)\b"), "asm-include macros are not allowed"),
    (re.compile(r"\.incbin\b"), ".incbin is not allowed"),
    (re.compile(r"__attribute__\s*\(\(\s*(section|naked)\b"), "section/naked attributes are not allowed"),
    (re.compile(r"(#\s*pragma|\b_Pragma)\b"), "pragmas are not allowed (ask the project admin to whitelist one)"),
    (re.compile(r"#\s*include\s*<"), "system includes are not allowed; everything you need is in context.h"),
]
_INCLUDE = re.compile(r'#\s*include\s*"([^"]+)"')


class SourceRejected(ValueError):
    pass


def check_source(source: str) -> None:
    """Refuse C that could match without being C (inline asm, asm-include macros, pragmas) or reach outside the
    sandbox (absolute or `..` includes): what the judge accepts must come from a compiler."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise SourceRejected(f"source too large (max {MAX_SOURCE_BYTES} bytes)")
    for inc in _INCLUDE.findall(source):
        if inc.startswith("/") or ".." in inc.split("/"):
            raise SourceRejected(f"include path not allowed: {inc}")
    code = _COMMENTS_AND_STRINGS.sub(" ", source)
    for pattern, why in _FORBIDDEN:
        if pattern.search(code):
            raise SourceRejected(why)


@dataclass
class CompilerConfig:
    cmd: list[str]
    timeout: float = 15.0
    sandbox: str = "auto"  # auto | bwrap | none
    ro_paths: tuple[str, ...] = ()
    include_dirs: tuple[str, ...] = ()  # absolute paths of extra (static) header dirs
    runtime32: str | None = None  # 32-bit x86 C runtime for old i386 compiler binaries (see toolchains)
    wsl: bool = False  # a Linux toolchain on Windows: runs inside WSL 2, `cmd` holds WSL paths (see wsl.py)
    intrinsics: str = ""  # module selected by the pinned toolchain, never by submitted source
    prelude: str = ""  # the toolchain's prelude (Toolchain.prelude)
    cplusplus: bool = False  # it compiles C++ too: what doesn't compile as C is tried as C++ (Toolchain.cplusplus)
    linux_vm: bool = False  # Linux-only compiler on macOS, isolated local VM jobs
    assembly_preprocess: tuple[str, ...] = ()
    assembly_compile: tuple[str, ...] = ()
    identity: tuple | None = None  # coordinator's project/function/target snapshot; enables in-flight sharing and scoped object reuse


@dataclass
class CompileResult:
    ok: bool
    obj: bytes | None
    log: str
    language: str = "c"  # "c++" when it compiled only as C++ (see compile_source)
    effective_settings: dict | None = None


def _sandbox_mode(cfg: CompilerConfig) -> str:
    if cfg.linux_vm:
        return "linux-vm"
    if cfg.wsl:
        return "wsl"
    if cfg.sandbox != "auto":
        return cfg.sandbox
    return "bwrap" if shutil.which("bwrap") else "none"


def _root(cfg: CompilerConfig, work: Path) -> str:
    """How the compiler sees the work folder (in WSL it works on a copy, see wsl.run)."""
    mode = _sandbox_mode(cfg)
    return "/work" if mode in ("bwrap", "linux-vm") else wsl.stage_dir() if mode == "wsl" else str(work)


def _wrap(cfg: CompilerConfig, work: Path, argv: list[str]) -> list[str]:
    mode = _sandbox_mode(cfg)
    if mode in ("none", "bwrap") and shutil.which("prlimit"):
        argv = ["prlimit", *LIMITS, "--", *argv]
    if mode == "none":
        return argv

    if mode == "bwrap":
        # /tmp first, so read-only mounts of paths that live under /tmp are not hidden by it.
        cmd = ["bwrap", "--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib64", "/lib64",
               "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev"]
        if cfg.runtime32:
            # i386 binaries hardcode /lib/ld-linux.so.2; give them a private /lib with the pinned loader,
            # and point the loader at the pinned 32-bit libraries. 64-bit tools ignore those libraries.
            cmd += ["--dir", "/lib", "--ro-bind", f"{cfg.runtime32}/ld-linux.so.2", "/lib/ld-linux.so.2",
                    "--ro-bind", cfg.runtime32, "/opt/rt32", "--setenv", "LD_LIBRARY_PATH", "/opt/rt32"]
        else:
            cmd += ["--symlink", "usr/lib", "/lib"]
        for p in cfg.ro_paths:
            cmd += ["--ro-bind", p, p]
        cmd += ["--bind", str(work), "/work", "--chdir", "/work", "--unshare-all", "--die-with-parent", "--new-session"]
        return [*cmd, "--", *argv]
    raise RuntimeError(f"unknown sandbox mode {mode!r}")


def _exec(cfg: CompilerConfig, work: Path, argv: list[str], timeout: float, cwd: Path | None = None, **stage):
    # Driver internals are opaque; do not pretend its assembler time is separable.
    mode = _sandbox_mode(cfg)
    with compile_timing.phase('driver_' + (mode if mode in ('linux-vm', 'wsl') else 'native')):
        return _exec_driver(cfg, work, argv, timeout, cwd, **stage)


def _exec_driver(cfg: CompilerConfig, work: Path, argv: list[str], timeout: float, cwd: Path | None = None, **stage):
    """`stage`: in WSL, run on a copy in WSL's own file system (wsl.run's outputs, inputs and stage)."""
    if _sandbox_mode(cfg) == "linux-vm":
        check = _CANCEL_CHECK.get()
        try:
            return linuxvm.run(argv, cwd or work, timeout, **stage, **({'check_cancel': check} if check else {}))
        except linuxvm.VMError as error:
            return subprocess.CompletedProcess(argv, 1, '', str(error))
    if _sandbox_mode(cfg) == "wsl":
        check = _CANCEL_CHECK.get()
        return wsl.run(argv, cwd or work, timeout, **stage, **({"check_cancel": check} if check else {}))
    # in the work folder, never in the app's own: KMC GCC's assembler (binutils 2.6) can't read its input when the
    # current directory is / ("Bad file descriptor"), as it can be for a program its launcher starts
    check = _CANCEL_CHECK.get()
    timeout = min(timeout, limits.compiler_time_limit())
    with compile_timing.phase('runtime_setup'):
        wrapped = _wrap(cfg, work, argv)
    with compile_timing.phase('process_launch'):
        proc = subprocess.Popen(wrapped, cwd=cwd or work, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                                start_new_session=os.name != "nt")
    from . import compiler_processes
    compiler_processes.track(proc.pid)
    started = time.monotonic()
    try:
        with limits.CompilerMemoryWatchdog(proc, timeout=timeout) as watchdog:
            while True:
                if check:
                    check()
                try:
                    stdout, stderr = proc.communicate(timeout=0.1)
                    if watchdog.timed_out:
                        raise subprocess.TimeoutExpired(proc.args, timeout)
                    if watchdog.exceeded:
                        return subprocess.CompletedProcess(proc.args, 1, stdout, stderr + "\n" + watchdog.message())
                    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    if time.monotonic() - started > timeout:
                        raise subprocess.TimeoutExpired(proc.args, timeout)
    except BaseException:
        if os.name != "nt":
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stop_process_tree(proc)
        raise
    finally:
        # A driver may exit before its children. Always reap its isolated group, not just the driver PID.
        if os.name != "nt":
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # communicate closes these on success, but cancellation can happen first.
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        compiler_processes.untrack(proc.pid)



def run_in(cfg: CompilerConfig, work: Path, argv: list[str], timeout: float = 300, *,
           inputs: list[str] | None = None, outputs: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a toolchain command inside `work` (the sandbox's /work), e.g. to build a whole exported project.
    Paths in `argv` must be relative to `work`. In WSL, naming the files it reads (`inputs`) and writes (`outputs`)
    lets old 32-bit compilers work on a copy in WSL's own file system, which they need (see wsl.run)."""
    return _exec(cfg, Path(work), argv, timeout, cwd=Path(work),
                 **({} if outputs is None else {"inputs": inputs, "outputs": outputs}))


@contextmanager
def _workspace(prefix):
    """Keep compiler scratch on an explicitly selected app data drive, not a full OS drive."""
    parent = os.environ.get('DECOMP_COMPILER_TMPDIR')
    if not parent and (home := os.environ.get('DECOMP_HOME')):
        parent = str(Path(home).expanduser() / '.decomp' / 'compiler-tmp')
    if parent:
        parent = Path(parent).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never silently fall back to another drive when the selected location is unavailable.
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent or None) as temporary:
        yield temporary


def _stable_inputs(inputs, staged_names=()):
    staged_names = set(staged_names)
    for part in inputs:
        # Translation phase two removes line splices before macro expansion.
        text = part.replace(b'\\\r\n', b'').replace(b'\\\n', b'')
        if any(word in text for word in (b'__TIME__', b'__DATE__', b'__TIMESTAMP__',
                                        b'__FILE__', b'__BASE_FILE__', b'__has_include', b'##', b'%:%:', b'??')):
            return False
        text = text.replace(b'%:', b'#')
        text = _COMMENTS_AND_STRINGS.sub(
            lambda match: ' ' if match.group().startswith('/') else match.group(),
            text.decode('utf-8', errors='replace')).encode()
        for directive in re.finditer(rb'(?m)^\s*#\s*(include(?:_next)?|import)\b([^\n]*)', text):
            include = re.match(rb'\s*"([^"\n]+)"', directive[2])
            if directive[1] == b'include_next' or include is None or include[1] not in staged_names:
                return False
    return True


def _captured_include_paths(command):
    for index, arg in enumerate(command):
        if arg == '-I' and command[index+1:index+2] == ['{include}']:continue
        if arg == '-I{include}':continue
        if arg.startswith(('-I', '-include', '-imacros', '-isystem', '-iquote', '-idirafter',
                           '-iprefix', '-iwithprefix', '-Wp,', '/I', '/FI', '@')):
            return False
    return True


def compile_source(cfg: CompilerConfig, context_header: str, source: str, trusted: bool = False) -> CompileResult:
    """Compile one function's C with the project's compiler in the sandbox, with the shared header as context.h.
    Agents' C (not `trusted`) is checked first. C++ is tried for what doesn't compile as C when the toolchain builds
    both."""
    compile_timing.add('requests', 1)
    if check := _CANCEL_CHECK.get():check()
    if not trusted:
        try:
            check_source(source)
        except SourceRejected as e:
            return CompileResult(False, None, f"rejected: {e}")

    from contextlib import nullcontext
    session = compile_sessions.current() if cfg.identity is not None and _ARTIFACT_SINK.get() is None else None
    setup_started = time.monotonic()
    prepared = session.prepare(cfg, context_header) if session else None
    if prepared is None:session = None
    with nullcontext(prepared) if session else _workspace("decompnet-") as tmp:
        work = Path(tmp)
        inc = work / "include"
        if not session:inc.mkdir()
        for d in (() if session else cfg.include_dirs):
            shutil.copytree(d, inc, dirs_exist_ok=True)
        for header in (() if session else inc.rglob("*")):
            if header.is_file() and header.suffix.lower() in (".h", ".hpp", ".hxx", ".inc", ".inl"):
                data = header.read_bytes()
                if b"\r\n" in data:  # old GCCs don't continue a macro before CRLF, even in copied headers
                    header.write_bytes(data.replace(b"\r\n", b"\n"))
        # LF on every platform: to the old GCCs a backslash before CRLF doesn't continue a line ("stray '\' in program")
        if not session:(inc / "context.h").write_text(context_header, encoding="utf-8", newline="\n")
        compile_timing.add('setup', time.monotonic() - setup_started)
        root = _root(cfg, work)
        def build():
            result = _compile_as(cfg, work, root, source, "c")
            # C++ for what doesn't compile as C (a class's method, new, templates): C stays C, so what matched as C
            # still compiles exactly as it did (GTA San Andreas is C++; 1,222 of its functions matched as C)
            if not result.ok and cfg.cplusplus and not result.log.startswith("compiler timed out") and "Compiler or build process exceeded" not in result.log:
                cpp = _compile_as(cfg, work, root, source, "cpp")
                if cpp.ok or _CPP.search(source):
                    result = cpp
            return result
        # Capture the actual staged headers, configuration and environment, never a guessed source-only key.
        # Cancellable searches reuse only their own completed results; artifact readers always run privately.
        share = cfg.identity is not None and _ARTIFACT_SINK.get() is None and (_CANCEL_CHECK.get() is None or session is not None)
        staged = (session.staged if session else [(str(path.relative_to(inc)).encode(), path.read_bytes()) for path in sorted(inc.rglob('*')) if path.is_file()]) if share else []
        intrinsic = importlib.import_module(cfg.intrinsics).header().encode() if share and cfg.intrinsics else b''
        inputs = [source.encode(), cfg.prelude.encode(), intrinsic, *(part for entry in staged for part in entry)]
        if share and _captured_include_paths(cfg.cmd) and _stable_inputs(
                [*inputs, *(arg.encode() for arg in cfg.cmd)], [name for name, _ in staged]):
            from .compile_sharing import run as shared_compile
            digest = hashlib.sha256(repr((cfg, sorted(os.environ.items()))).encode())
            for part in inputs:
                digest.update(len(part).to_bytes(8,'little'))
                digest.update(part)
            # Mutable custom tool executables cannot reuse a running invocation after replacement.
            for arg in cfg.cmd:
                if os.path.isabs(arg) and Path(arg).is_file():
                    st = Path(arg).stat()
                    digest.update(repr((arg, st.st_size, st.st_mtime_ns)).encode())
            key = digest.digest()
            if check := _CANCEL_CHECK.get():check()
            result = session.get(key) if session else None
            if result is not None:
                compile_timing.add('cache_hits', 1)
            elif session:
                from .compile_sharing import current as shared_group
                group = shared_group()
                result = group.run(key, build, _CANCEL_CHECK.get()) if group else build()
                if check := _CANCEL_CHECK.get():check()
                session.put(key, result)
            else:
                result = shared_compile(key, build)
        else:
            result = build()
        if sink := _ARTIFACT_SINK.get():
            sink(work)
        return result


_CPP = re.compile(r"::|\bclass\b|\btemplate\b|\bnew\b|\bdelete\b|\bvirtual\b|\boperator\b|\bnamespace\b|\bthis\b")


def _compile_as(cfg: CompilerConfig, work: Path, root: str, source: str, ext: str) -> CompileResult:
    name = f"func.{ext}"
    intrinsics = importlib.import_module(cfg.intrinsics).header() if cfg.intrinsics else ""
    (work / name).write_text(cfg.prelude + intrinsics + f'#include "context.h"\n#line 1 "{name}"\n' + source + "\n",
                             encoding="utf-8", newline="\n")
    subst = {"{src}": f"{root}/{name}", "{obj}": f"{root}/func.o", "{include}": f"{root}/include"}
    argv = [subst.get(a, a) for a in cfg.cmd]
    argv = [a.replace("{include}", subst["{include}"]) for a in argv]
    (work / "func.o").unlink(missing_ok=True)
    try:
        with _slot():
            with span("compile_execute"), compile_timing.phase("execute"):
                proc = _exec(cfg, work, argv, cfg.timeout,
                         **({"outputs": ["func.o"], "stage": root} if _sandbox_mode(cfg) in ("wsl", "linux-vm") else {}))
    except subprocess.TimeoutExpired:
        return CompileResult(False, None, f"compiler timed out after {cfg.timeout}s")
    log = (proc.stdout + proc.stderr).replace(root + "/", "")
    obj = work / "func.o"
    if proc.returncode != 0 or not obj.exists():
        return CompileResult(False, None, log[-4000:] or f"compiler exited with {proc.returncode}")
    return CompileResult(True, obj.read_bytes(), log[-2000:], "c++" if ext == "cpp" else "c")


def compile_assembly(cfg: CompilerConfig, prelude: str, source: str) -> CompileResult:
    """Preprocess supplied assembly with the pinned driver, then assemble it under the normal limits.

    References must already be flattened from authorized roots. No assembler file
    directives may read arbitrary files or inject a binary as purported source.
    This result never passes through the C match/promotion path.
    """
    text=prelude+'\n'+source
    try:
        check_source(text)
        if re.search(r'(?im)(?:^\s*#\s*include\b|\.(?:include|incbin)\b)',text):
            raise SourceRejected('Flatten includes from the selected source roots before assembly verification.')
    except SourceRejected as error:return CompileResult(False,None,'rejected: '+str(error))
    with _workspace('decompnet-assembly-') as tmp:
        work=Path(tmp);root=_root(cfg,work)
        (work/'input.c').write_text(text,encoding='utf-8',newline='\n')
        def argv(src,obj,preprocess=False):
            subst={'{src}':root+'/'+src,'{obj}':root+'/'+obj,'{include}':root}
            explicit=cfg.assembly_preprocess if preprocess else cfg.assembly_compile
            args=[a.replace('{include}',root) if a not in subst else subst[a] for a in explicit or cfg.cmd]
            if preprocess and not explicit:
                if Path(args[0]).name in ('sh','bash','cmd.exe') or '-c' not in args:raise SourceRejected('Assembly verification needs a compiler driver with a -c command.')
                args[args.index('-c')]='-E'
            return args
        try:
            for src,obj,pp in [('input.c','func.s',True),('func.s','func.o',False)]:
                if not pp and re.search(r'(?i)\.(?:include|incbin)\b',_COMMENTS_AND_STRINGS.sub(' ',(work/src).read_text())):
                    raise SourceRejected('Preprocessed assembly must not include files or binary data.')
                args=argv(src,obj,pp)
                with _slot(), compile_timing.phase('assembly_preprocess' if pp else 'assembly_execute'):
                    proc=_exec(cfg,work,args,cfg.timeout,**({'inputs':[src],'outputs':[obj],'stage':root} if _sandbox_mode(cfg) in ('wsl','linux-vm') else {}))
                log=(proc.stdout+proc.stderr).replace(root+'/','')
                # IDO writes -E output to stdout even with -o; GCC writes the file.
                if pp and proc.returncode==0 and not (work/obj).is_file() and proc.stdout:
                    if len(proc.stdout.encode())>16*1024*1024:return CompileResult(False,None,'Preprocessed assembly exceeds the verification limit.')
                    (work/obj).write_text(proc.stdout,encoding='utf-8',newline='\n')
                if proc.returncode or not (work/obj).is_file():return CompileResult(False,None,log or 'Compiler driver did not produce '+obj)
                if (work/obj).stat().st_size>16*1024*1024:return CompileResult(False,None,'Assembly output exceeds the verification limit.')
        except (subprocess.TimeoutExpired,SourceRejected) as error:return CompileResult(False,None,str(error))
        return CompileResult(True,(work/'func.o').read_bytes(),log,language='assembly')
