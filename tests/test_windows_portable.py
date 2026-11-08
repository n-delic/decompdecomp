"""The portable executable keeps working after relocation, without a registered Python install."""
import base64
import os
import subprocess
import sys
from pathlib import Path

import pytest

from decompnet.app import launcher

ROOT = Path(__file__).resolve().parents[1]


def test_portable_app_does_not_create_shortcuts(monkeypatch):
    monkeypatch.setenv('DECOMP_PORTABLE', '1')
    monkeypatch.setattr(launcher.edition, 'DEV', False)
    monkeypatch.setattr(launcher.Path, 'home', lambda: pytest.fail('Portable setup must not write launcher files'))
    assert 'no shortcuts were installed' in launcher.install_launcher()


@pytest.mark.skipif(sys.platform != 'win32', reason='needs the native Windows C# compiler')
def test_portable_exe_forwards_arguments_and_exit_status_after_relocation(tmp_path):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        pytest.skip('needs the .NET Framework compiler')
    original = tmp_path / 'original'
    original.mkdir()
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                    '/reference:System.Windows.Forms.dll',
                    '/win32icon:' + str(ROOT / 'src/decompnet/app/static/favicon.ico'), '/out:' + str(original / 'decompdecomp.exe'),
                    str(ROOT / 'packaging/windows/portable-launcher.cs')], check=True, capture_output=True)
    helper = tmp_path / 'probe.cs'
    helper.write_text('''using System;
using System.IO;
using System.Text;
class Probe {
    static int Main(string[] args) {
        var lines = new string[args.Length + 3];
        for (int i = 0; i < args.Length; i++)
            lines[i] = Convert.ToBase64String(Encoding.UTF8.GetBytes(args[i]));
        lines[args.Length] = Environment.GetEnvironmentVariable("DECOMP_PORTABLE");
        lines[args.Length + 1] = Environment.GetEnvironmentVariable("PYTHONHOME") ?? "absent";
        lines[args.Length + 2] = Environment.GetEnvironmentVariable("PYTHONPATH") ?? "absent";
        File.WriteAllLines(Environment.GetEnvironmentVariable("PORTABLE_PROBE_OUTPUT"), lines);
        return 37;
    }
}
''')
    (original / 'python').mkdir()
    (original / 'decomp-launch.pyw').touch()
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64',
                    '/out:' + str(original / 'python/pythonw.exe'), str(helper)], check=True, capture_output=True)
    relocated = tmp_path / 'moved portable app ü'
    original.rename(relocated)
    output = tmp_path / 'arguments.txt'
    arguments = ['', 'space in argument', 'embedded"quote', 'trailing\\', 'slash\\"quote', '日本語']
    env = dict(os.environ, PORTABLE_PROBE_OUTPUT=str(output), PYTHONHOME='unrelated', PYTHONPATH='unrelated')
    result = subprocess.run([str(relocated / 'decompdecomp.exe'), *arguments], env=env, timeout=30)
    assert result.returncode == 37
    lines = output.read_text(encoding='utf-8-sig').splitlines()
    actual = [base64.b64decode(line).decode('utf-8') for line in lines[:-3]]
    assert actual == [str(relocated / 'decomp-launch.pyw'), *arguments]
    assert lines[-3:] == ['1', 'absent', 'absent']
