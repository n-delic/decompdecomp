"""Generated Ninja commands preserve spaced shell arguments and intentional Ninja variables."""
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet import toolchains
from decompnet.consoles.gc_wii import template_paths
from decompnet.forks.project import ForkError


def generator(root, text):
    path=root/'tools/project.py';path.parent.mkdir(parents=True)
    path.write_text(text)
    template_paths.prepare(root)
    return path


FRAGMENT = '''
def commands(n, dtk, wrapper, mwcc, mwld, sjiswrap, gnu_as):
    n.variable("configure_args", sys.argv[1:])
    n.variable("python", f'"{sys.executable}"')
    return [f"{dtk} dol split $in $out_dir", f"{wrapper} {mwcc} $in", f"{mwld} $in", f"{sjiswrap} $in", f"{gnu_as} $in"]
'''


def test_generated_ninja_runs_tools_and_reconfigure_arguments_with_spaces(tmp_path):
    ninja=shutil.which('ninja') or str(Path(sys.executable).parent/'ninja')
    if not Path(ninja).is_file():pytest.skip('needs ninja')
    root=tmp_path/'project with spaces';root.mkdir()
    path=generator(root,FRAGMENT)
    namespace={'sys':sys};exec(path.read_text(),namespace)
    tools=tmp_path/"Application Support's $tools";tools.mkdir()
    executable=tools/'fake dtk'
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DECOMP_TEST_RESULT"\n')
    executable.chmod(0o755)
    class Writer:
        def __init__(self):self.variables={}
        def variable(self,key,value):self.variables[key]=value
    writer=Writer()
    previous=sys.argv
    try:
        sys.argv=['configure.py','--dtk',str(executable),'--compilers',str(tools/'compiler files')]
        commands=namespace['commands'](writer,*[executable]*6)
    finally:sys.argv=previous
    output=root/'args.json'
    # Exercise a real Ninja -> shell -> executable chain, including literal dollars.
    (root/'build.ninja').write_text('rule split\n  command = '+commands[0]+'\nbuild result: split config.yml\n  out_dir = output\n')
    (root/'config.yml').write_text('')
    result=subprocess.run([ninja,'-f','build.ninja'],cwd=root,capture_output=True,text=True,
                          env={**os.environ,'DECOMP_TEST_RESULT':str(output)})
    assert result.returncode==0,result.stdout+result.stderr
    assert output.read_text().splitlines()==['dol','split','config.yml','output']
    # Execute Ninja's reconfigure argument variable through the shell as well.
    capture=root/'capture.py';capture.write_text('import sys,json;open("args.json","w").write(json.dumps(sys.argv[1:]))')
    (root/'build.ninja').write_text('args = '+writer.variables['configure_args']+'\nrule configure\n  command = '+writer.variables['python']+' capture.py $args\nbuild regenerate: configure\n')
    result=subprocess.run([ninja],cwd=root,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(output.read_text())==['--dtk',str(executable),'--compilers',str(tools/'compiler files')]


def test_pinned_template_patch_is_valid_and_idempotent(tmp_path):
    installed=toolchains.installed('dtk-template')
    if installed is None:pytest.skip('needs pinned dtk-template')
    original=(toolchains.path('dtk-template')/'tools/project.py').read_text()
    path=generator(tmp_path,original)
    patched=path.read_text();ast.parse(patched)
    assert '{dtk}' not in patched and '{mwcc}' not in patched
    assert '{_decomp_shell_arg(mwcc, _DECOMP_MW_VARS)}' in patched
    template_paths.prepare(tmp_path)
    assert path.read_text()==patched
    assert (toolchains.path('dtk-template')/'tools/project.py').read_text()==original


def test_compiler_version_variable_survives_ninja_expansion(tmp_path):
    generator(tmp_path,FRAGMENT)
    namespace={};exec(template_paths.HELPER,namespace)
    quote=namespace['_decomp_shell_arg']
    assert '$mw_version' in quote(tmp_path/'Application Support/$mw_version/mwcceppc.exe',('mw_version',))
    assert '$$mw_version' not in quote(tmp_path/'Application Support/$mw_version/mwcceppc.exe',('mw_version',))
    assert '$$literal' in quote(tmp_path/'$literal/tool')


def test_unfamiliar_template_is_not_partially_rewritten(tmp_path):
    path=tmp_path/'tools/project.py';path.parent.mkdir();path.write_text('different generator')
    with pytest.raises(ForkError,match='unfamiliar'):template_paths.prepare(tmp_path)
    assert path.read_text()=='different generator'


def test_imported_compiler_version_is_retained_without_rom_profile_assumption():
    from decompnet.match_bundle import compiler_identity
    value=compiler_identity('GC/1.3.2')
    assert value['build_compiler']=='GC/1.3.2' and value['toolchain'] is None
    assert compiler_identity('GC/2.0')!=value
