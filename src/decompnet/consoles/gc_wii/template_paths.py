"""Quote the pinned template's shell arguments without changing Ninja dependency paths."""
from pathlib import Path

from ...forks.project import ForkError

MARKER = '# decompdecomp: quote template shell paths v1'
HELPER = '''

# decompdecomp: quote template shell paths v1
_DECOMP_MW_VARS = ('mw_version',)
def _decomp_shell_arg(value, ninja_vars=()):
    import os
    import shlex
    import subprocess
    text = os.fspath(value)
    quoted = subprocess.list2cmdline([text]) if os.name == 'nt' else shlex.quote(text)
    quoted = quoted.replace('$', '$$')
    for name in ninja_vars:
        quoted = quoted.replace('$$' + name, '$' + name)
    return quoted
'''


def prepare(root: Path) -> None:
    """Patch only the app-generated project's copy, never the cached toolchain."""
    path = root/'tools/project.py'
    text = path.read_text(encoding='utf-8')
    if MARKER in text:return
    arguments = 'n.variable("configure_args", sys.argv[1:])'
    if arguments not in text or '{dtk}' not in text:
        raise ForkError('The installed project template has an unfamiliar build generator. Update decompdecomp before importing this game.')
    text = text.replace(arguments, 'n.variable("configure_args", " ".join(_decomp_shell_arg(arg) for arg in sys.argv[1:]))')
    text = text.replace('n.variable("python", f\'"{sys.executable}"\')', 'n.variable("python", _decomp_shell_arg(sys.executable))')
    for name in ('dtk', 'wrapper', 'sjiswrap', 'gnu_as', 'objdiff', 'download_tool', 'decompctx',
                 'transform_dep', 'configure_script', 'changes_fmt', 'report_baseline_path'):
        text = text.replace('{'+name+'}', '{_decomp_shell_arg('+name+')}')
    for name in ('mwcc', 'mwld'):
        # These paths deliberately contain a Ninja variable, not a shell variable.
        text = text.replace('{'+name+'}', "{_decomp_shell_arg("+name+", _DECOMP_MW_VARS)}")
    # A separate helper keeps the pinned generator's path-valued dependency lists
    # intact. Ninja still escapes those; only shell commands need shell quoting.
    compile(text + HELPER, str(path), 'exec')
    path.write_text(text + HELPER, encoding='utf-8')
