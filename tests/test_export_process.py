"""The app can observe and stop an export while the child build is still running."""
import sys

import pytest

from decompnet.rom import export_process

CHILD = """
import time
from pathlib import Path
from decompnet.rom.export_process import check_build_stop, report_build

report_build(4, 'Building exported objects', done=3, total=8)
deadline = time.monotonic() + 5
while not Path('continue').exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('Parent never received live progress')
    try:
        check_build_stop()
    except RuntimeError:
        Path('stopped').touch()
        raise
    time.sleep(.01)
report_build(5, 'Linking and comparing the exported image')
print('identical')
"""


def test_verify_reports_objects_before_the_build_exits(tmp_path):
    events = []

    def log(_):
        pass

    def progress(event):
        events.append(event)
        if event.get('done') == 3:
            (tmp_path / 'continue').touch()

    log.progress = progress
    proc = export_process.verify([sys.executable, '-c', CHILD], tmp_path, log)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == 'identical\n'
    assert [event['step'] for event in events] == [4, 4, 5]
    assert all(event['steps'] == 5 for event in events)
    assert events[1]['done'] == 3 and events[1]['total'] == 8


def test_verify_can_cancel_while_waiting_for_an_object(tmp_path):
    stopped = False

    def log(_):
        pass

    def progress(event):
        nonlocal stopped
        stopped = event.get('done') == 3

    def check_cancel():
        if stopped:
            raise RuntimeError('Stopped during rebuild')

    log.progress = progress
    log.check_cancel = check_cancel
    with pytest.raises(RuntimeError, match='Stopped during rebuild'):
        export_process.verify([sys.executable, '-c', CHILD], tmp_path, log)
    assert not (tmp_path / 'continue').exists()
    assert (tmp_path / 'stopped').exists()
