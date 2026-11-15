"""Failure diagnostics stay bounded and available without opening a log file."""
from decompnet.app.support import recent_log


def test_recent_log_is_bounded_and_keeps_latest_lines(tmp_path):
    path=tmp_path/'app.log'
    path.write_text('\n'.join(f'line {i} '+'x'*100 for i in range(2000)))
    tail=recent_log(path)
    assert len(tail)<=6000 and len(tail.splitlines())<=30
    assert 'line 1999 ' in tail and 'line 0 ' not in tail
    assert recent_log(tmp_path/'missing')==''
    assert recent_log(None)==''
