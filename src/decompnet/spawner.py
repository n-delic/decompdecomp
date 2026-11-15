"""Starting tools without forking the app: `Popen` and `run` that use macOS's posix_spawn wherever the standard
library would fork.

A compile is a short tool run, and the app starts thousands of them. On macOS, CPython 3.12 forks the whole app to
start each one unless the call needs none of: a working folder, a new session, closing the app's other file
descriptors (subprocess's own condition before it uses os.posix_spawn). A compile needs all three. Forking a
1.4 GB process with 125 threads copies its page tables and takes every malloc zone's lock around the fork, so
concurrent compiles queued on each other there (a CPU sample of the live app: about 245 samples in the zone lock
inside fork_exec). macOS's posix_spawn is a system call that starts the new program without copying the app, and
macOS adds what a compile needs: a working folder (`posix_spawn_file_actions_addchdir_np`), a new session
(`POSIX_SPAWN_SETSID`) and closing every descriptor not handed over (`POSIX_SPAWN_CLOEXEC_DEFAULT`).

Only the launch differs: the object is a real `subprocess.Popen`, so communicate, wait, kill, the pipes and the
compiler process tracking work as before. Anything the fast path doesn't cover (a preexec function, user or group
changes, passed descriptors, a shell, a standard stream handed over as itself) uses the standard library's own
launch, as does every other system: Linux's CPython already starts tools with vfork, and Windows has no fork.
`DECOMP_POSIX_SPAWN=0` turns the fast path off.
"""
from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import threading

POSIX_SPAWN_SETSIGDEF = 0x0004
POSIX_SPAWN_SETSIGMASK = 0x0008
POSIX_SPAWN_SETSID = 0x0400
POSIX_SPAWN_CLOEXEC_DEFAULT = 0x4000
# subprocess's restore_signals: the app ignores SIGPIPE, and an ignored signal stays ignored across exec
RESTORED_SIGNALS = tuple(getattr(signal, name) for name in ('SIGPIPE', 'SIGXFSZ') if hasattr(signal, name))
_MISSING_PROGRAM = (errno.ENOENT, errno.ENOTDIR)

_lock = threading.Lock()
_libc = None  # the C library once it's checked, False where the fast path can't be used


_SIGNATURE = ('self', 'args', 'executable', 'preexec_fn', 'close_fds', 'pass_fds', 'cwd', 'env', 'startupinfo',
              'creationflags', 'shell', 'p2cread', 'p2cwrite', 'c2pread', 'c2pwrite', 'errread', 'errwrite',
              'restore_signals', 'gid', 'gids', 'uid', 'umask', 'start_new_session', 'process_group')


def _standard_signature() -> bool:
    """Popen._execute_child is private: a Python that changed it keeps the standard library's launch."""
    import inspect
    try:
        return tuple(inspect.signature(subprocess.Popen._execute_child).parameters) == _SIGNATURE
    except (TypeError, ValueError):
        return False


def _setting_enabled() -> bool:
    return os.environ.get('DECOMP_POSIX_SPAWN', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def _load():
    """The C library's spawn functions, or False where this isn't macOS or a function is missing."""
    global _libc
    if _libc is None:
        with _lock:
            if _libc is None:
                libc = False
                if sys.platform == 'darwin' and _standard_signature():
                    try:
                        candidate = ctypes.CDLL(None, use_errno=True)
                        handle = ctypes.POINTER(ctypes.c_void_p)
                        for name, argtypes in (
                                ('posix_spawn', (ctypes.POINTER(ctypes.c_int), ctypes.c_char_p, handle, handle,
                                                 ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p))),
                                ('posix_spawn_file_actions_init', (handle,)),
                                ('posix_spawn_file_actions_destroy', (handle,)),
                                ('posix_spawn_file_actions_addclose', (handle, ctypes.c_int)),
                                ('posix_spawn_file_actions_adddup2', (handle, ctypes.c_int, ctypes.c_int)),
                                ('posix_spawn_file_actions_addinherit_np', (handle, ctypes.c_int)),
                                ('posix_spawn_file_actions_addchdir_np', (handle, ctypes.c_char_p)),
                                ('posix_spawnattr_init', (handle,)),
                                ('posix_spawnattr_destroy', (handle,)),
                                ('posix_spawnattr_setflags', (handle, ctypes.c_short)),
                                ('posix_spawnattr_setsigdefault', (handle, ctypes.POINTER(ctypes.c_uint32))),
                                ('posix_spawnattr_setsigmask', (handle, ctypes.POINTER(ctypes.c_uint32)))):
                            function = getattr(candidate, name)
                            function.argtypes, function.restype = argtypes, ctypes.c_int
                        libc = candidate
                    except (OSError, AttributeError):
                        libc = False
                _libc = libc
    return _libc


def available() -> bool:
    """Whether tools start through posix_spawn here (macOS, unless DECOMP_POSIX_SPAWN=0)."""
    return _setting_enabled() and bool(_load())


def _check(result: int, what: str) -> None:
    if result:
        raise OSError(result, f'{what}: {os.strerror(result)}')


def _signal_set(signals) -> ctypes.c_uint32:
    """A macOS sigset_t (one bit per signal, signal 1 in bit 0)."""
    mask = 0
    for number in signals:
        mask |= 1 << (int(number) - 1)
    return ctypes.c_uint32(mask)


def _spawn(libc, candidates, argv, envp, cwd, *, close_fds, restore_signals, start_new_session, closes, dup2s,
           inherited) -> int:
    """posix_spawn the first of `candidates` (program paths, as bytes) that exists, like execvpe tries PATH."""
    actions, attributes = ctypes.c_void_p(), ctypes.c_void_p()
    _check(libc.posix_spawn_file_actions_init(ctypes.byref(actions)), 'posix_spawn_file_actions_init')
    try:
        _check(libc.posix_spawnattr_init(ctypes.byref(attributes)), 'posix_spawnattr_init')
        try:
            for fd in closes:
                _check(libc.posix_spawn_file_actions_addclose(ctypes.byref(actions), fd), 'addclose')
            for fd, target in dup2s:
                _check(libc.posix_spawn_file_actions_adddup2(ctypes.byref(actions), fd, target), 'adddup2')
            if close_fds:
                # Every descriptor not named here is closed in the new program, atomically, unlike close_fds after
                # a fork: a standard stream not redirected is still the app's own.
                for fd in inherited:
                    _check(libc.posix_spawn_file_actions_addinherit_np(ctypes.byref(actions), fd), 'addinherit')
            if cwd is not None:
                _check(libc.posix_spawn_file_actions_addchdir_np(ctypes.byref(actions), cwd), 'addchdir')
            flags = POSIX_SPAWN_SETSIGMASK
            empty = _signal_set(())
            _check(libc.posix_spawnattr_setsigmask(ctypes.byref(attributes), ctypes.byref(empty)), 'setsigmask')
            if restore_signals:
                defaults = _signal_set(RESTORED_SIGNALS)
                _check(libc.posix_spawnattr_setsigdefault(ctypes.byref(attributes), ctypes.byref(defaults)),
                       'setsigdefault')
                flags |= POSIX_SPAWN_SETSIGDEF
            if start_new_session:
                flags |= POSIX_SPAWN_SETSID
            if close_fds:
                flags |= POSIX_SPAWN_CLOEXEC_DEFAULT
            _check(libc.posix_spawnattr_setflags(ctypes.byref(attributes), flags), 'setflags')
            c_argv = (ctypes.c_char_p * (len(argv) + 1))(*argv, None)
            c_envp = (ctypes.c_char_p * (len(envp) + 1))(*envp, None)
            pid = ctypes.c_int()
            failure = errno.ENOENT
            for program in candidates:
                failure = libc.posix_spawn(ctypes.byref(pid), program, ctypes.byref(actions),
                                           ctypes.byref(attributes), c_argv, c_envp)
                if not failure:
                    return pid.value
                if failure not in _MISSING_PROGRAM:
                    break
            raise OSError(failure, os.strerror(failure))
        finally:
            libc.posix_spawnattr_destroy(ctypes.byref(attributes))
    finally:
        libc.posix_spawn_file_actions_destroy(ctypes.byref(actions))


class Popen(subprocess.Popen):
    """subprocess.Popen, started with posix_spawn on macOS (see the module's docstring)."""

    if os.name != 'nt':
        def _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds, cwd, env, startupinfo,
                           creationflags, shell, p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
                           restore_signals, gid, gids, uid, umask, start_new_session, process_group):
            standard = (p2cread, c2pwrite, errwrite)
            libc = _load() if _setting_enabled() else False
            if (not libc or shell or preexec_fn is not None or pass_fds or gid is not None or gids is not None
                    or uid is not None or umask >= 0 or process_group != -1
                    or any(0 <= fd <= 2 for fd in standard)
                    or isinstance(args, (str, bytes, os.PathLike))):
                return super()._execute_child(
                    args, executable, preexec_fn, close_fds, pass_fds, cwd, env, startupinfo, creationflags, shell,
                    p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite, restore_signals, gid, gids, uid, umask,
                    start_new_session, process_group)
            args = list(args)
            if executable is None:
                executable = args[0]
            sys.audit('subprocess.Popen', executable, args, cwd, env)
            argv = [os.fsencode(arg) for arg in args]
            source = os.environ if env is None else env
            envp = []
            for key, value in source.items():
                key = os.fsencode(key)
                if b'=' in key:
                    raise ValueError('illegal environment variable name')
                envp.append(key + b'=' + os.fsencode(value))
            program = os.fsencode(executable)
            candidates = ((program,) if os.path.dirname(program) else
                          tuple(os.path.join(os.fsencode(folder), program) for folder in os.get_exec_path(env)))
            folder = None if cwd is None else os.fsencode(cwd)
            try:
                self.pid = _spawn(
                    libc, candidates, argv, envp, folder, close_fds=close_fds, restore_signals=restore_signals,
                    start_new_session=start_new_session,
                    closes=[fd for fd in (p2cwrite, c2pread, errread) if fd != -1],
                    dup2s=[(fd, target) for fd, target in ((p2cread, 0), (c2pwrite, 1), (errwrite, 2)) if fd != -1],
                    inherited=[target for fd, target in zip(standard, (0, 1, 2)) if fd == -1])
            except OSError as error:
                # Say which name was missing, as the standard library does: the folder, else the program.
                if error.errno in _MISSING_PROGRAM and folder is not None and not os.path.isdir(folder):
                    raise OSError(error.errno, os.strerror(error.errno), os.fsdecode(cwd)) from None
                raise OSError(error.errno, os.strerror(error.errno), os.fsdecode(executable)) from None
            self._child_created = True
            # The child's ends of the pipes are its own now (on a failure Popen's own cleanup closes them).
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)


def run(*popenargs, input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """subprocess.run, started by this module's Popen."""
    if input is not None:
        if kwargs.get('stdin') is not None:
            raise ValueError('stdin and input arguments may not both be used.')
        kwargs['stdin'] = subprocess.PIPE
    if capture_output:
        if kwargs.get('stdout') is not None or kwargs.get('stderr') is not None:
            raise ValueError('stdout and stderr arguments may not be used with capture_output.')
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    with Popen(*popenargs, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            if os.name == 'nt':
                exc.stdout, exc.stderr = process.communicate()
            else:
                process.wait()
            raise
        except BaseException:
            process.kill()
            raise
        code = process.poll()
        if check and code:
            raise subprocess.CalledProcessError(code, process.args, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(process.args, code, stdout, stderr)
