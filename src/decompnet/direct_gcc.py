"""Old GCC's own steps run directly, without its driver, when that provably builds the same object.

An old `gcc` driver only starts three programs (the preprocessor, cc1 and the assembler) through temporary files,
and costs a process of its own each compile: about 2 of a KMC GCC compile's 15.6 ms on an M-series Mac, with a deep
pass compiling hundreds a second. The driver's exact commands are learned once per compiler command (its `-v`
report on a probe), then the probe is compiled both ways and the plan is used only if the two objects are
byte-identical. Anything unusual (another driver, a sandbox, a different set of steps) keeps the driver.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_PLANS: dict = {}
_LOCK = threading.Lock()
ROOT = '\x00ROOT\x00'
TOOL = '\x00TOOL\x00'
STEPS = ('cpp', 'cccp', 'cc1', 'as')
PROBE = '''typedef struct { int a; short b; float c; double d; } S;
extern S table[]; extern int g(int, ...);
static int h(unsigned char *p, int n) { int s = 0; while (n--) s += *p++ << (n & 7); return s; }
int probe(int x, S *s) {
    int i, t = 0;
    for (i = 0; i < x; i++) { t += table[i].a * s->b; if (t > 99) t -= g(i, s->d); }
    s->c = (float)t * 1.5f; s->d += 0.25;
    return t + h((unsigned char *)s, sizeof *s);
}
'''


def _tool_dir(argv):
    for arg in argv:
        if arg.startswith('-B') and len(arg) > 2:
            return arg[2:].rstrip('/')
    return None


def eligible(argv, sandbox):
    return (sandbox == 'none' and Path(argv[0]).name == 'gcc' and _tool_dir(argv) is not None
            and '-c' in argv and '-o' in argv)


def _template(argv, root):
    return tuple(arg.replace(root, ROOT) for arg in argv)


def _learn(template):
    tool = _tool_dir(template)
    with tempfile.TemporaryDirectory(prefix='decompnet-gcc-plan-') as tmp:
        work = Path(tmp)
        (work / 'include').mkdir()
        (work / 'include' / 'context.h').write_text('')
        (work / 'func.c').write_text('#include "context.h"\n' + PROBE)
        argv = [arg.replace(ROOT, tmp) for arg in template]
        report = subprocess.run([argv[0], '-v', *argv[1:]], cwd=tmp, capture_output=True, text=True, timeout=60)
        if report.returncode != 0 or not (work / 'func.o').is_file():
            return None
        driver = (work / 'func.o').read_bytes()
        steps = []
        for line in report.stderr.splitlines():
            if not line.startswith(' ' + tool + '/'):
                continue
            words = line.strip().replace(tool, TOOL).split(' ')
            program = Path(words[0].replace(TOOL, tool)).name
            if program not in STEPS:
                continue  # (cpp's report of its include folders, under the same folder)
            words = [w.replace(TOOL, tool) for w in words]
            if '-v' in words[1:]:
                words.remove('-v')  # (added by the probe's own -v)
            steps.append(words)
        if [Path(s[0]).name for s in steps] not in (['cpp', 'cc1', 'as'], ['cccp', 'cc1', 'as']):
            return None
        temporary = re.compile(r'\S*/cc\w{6}\.(i|s)$')
        plan = []
        for step in steps:
            out = []
            for word in step:
                found = temporary.match(word)
                if found:
                    word = f'{ROOT}/func.{found.group(1)}'
                out.append(word.replace(tmp, ROOT))
            plan.append(out)
        # The proof: the probe built both ways, byte for byte. Otherwise the driver stays.
        (work / 'func.o').unlink()
        if _run(plan, tmp, 60).returncode != 0 or not (work / 'func.o').is_file():
            return None
        if (work / 'func.o').read_bytes() != driver:
            return None
        return tuple(tuple(step) for step in plan)


class _Done:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _run(plan, root, timeout, run=None):
    out, err = [], []
    for step in plan:
        argv = [word.replace(ROOT, root) for word in step]
        proc = (run(argv) if run is not None else
                subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout))
        out.append(proc.stdout or '')
        err.append(proc.stderr or '')
        if proc.returncode != 0:
            return _Done(proc.returncode, ''.join(out), ''.join(err))
    for leftover in ('func.i', 'func.s'):
        try:
            os.unlink(os.path.join(root, leftover))
        except OSError:
            pass
    return _Done(0, ''.join(out), ''.join(err))


def plan(argv, root, sandbox):
    """The driver's steps for this command with `root` as the work folder, or None: use the driver."""
    if os.environ.get('DECOMP_DIRECT_GCC', '1') == '0' or not eligible(argv, sandbox):
        return None
    template = _template(argv, root)
    with _LOCK:
        if template in _PLANS:
            return _PLANS[template]
    try:
        learned = _learn(template) if shutil.which(template[0]) or Path(template[0]).is_file() else None
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        learned = None
    with _LOCK:
        _PLANS[template] = learned
    return learned


def run(steps, root, execute):
    """Run a learned plan in `root`; `execute(argv)` runs one step as the driver would have run (sandbox, limits,
    timeout) and returns a completed process."""
    return _run(steps, root, None, execute)
