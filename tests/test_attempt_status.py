import threading

from decompnet import attempt_status


def test_status_is_scoped_to_request_function_and_resets_after_errors():
    import pytest
    seen=[]
    with pytest.raises(ValueError), attempt_status.watch('f',seen.append):
        attempt_status.update('saving','f')  # A startup recheck is not our result.
        attempt_status.update('compiling','other')
        attempt_status.update('compiling','f')
        attempt_status.update('compiling','f')
        attempt_status.update('checking','f')
        attempt_status.update('saving','f')
        raise ValueError('test')
    attempt_status.update('compiling','f')
    assert [v['stage'] for v in seen]==['waiting','compiling','checking','saving']
    assert all(v['function']=='f' for v in seen)


def test_observers_do_not_leak_between_threads():
    seen=[]
    with attempt_status.watch('f',seen.append):
        t=threading.Thread(target=lambda:attempt_status.update('compiling','f'))
        t.start();t.join()
    assert [v['stage'] for v in seen]==['waiting']
