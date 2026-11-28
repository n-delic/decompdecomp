import types

import pytest

from decompnet.app import maintenance_tasks, similar_phase
from decompnet.network import NetworkError


def job(**fields):
    return types.SimpleNamespace(**fields)


def test_automatic_width_is_unchanged():
    assert similar_phase.donor_width(job(), 100, 8, 7) == 7
    assert similar_phase.donor_width(job(), 3, 8, 7) == 3


def test_requested_workers_override_compile_limit_but_not_targets():
    assert similar_phase.donor_width(job(donor_workers=32), 100, 8, 7) == 32
    assert similar_phase.donor_width(job(donor_workers=32), 10, 8, 7) == 10


def test_maximum_uses_the_deep_lane_width():
    from decompnet.app import deep_throughput

    wanted = deep_throughput.lanes(True)
    assert similar_phase.donor_width(job(donor_maximum=True), 10_000, 8, 7) == wanted
    assert similar_phase.donor_width(job(donor_maximum=True, donor_workers=5), 10_000, 8, 7) == 5


def test_donor_options_validate():
    assert maintenance_tasks.donor_options(0, False) == (0, False)
    assert maintenance_tasks.donor_options(12, True) == (12, True)
    for bad in (-1, 257, "4", True, 1.5):
        with pytest.raises(NetworkError):
            maintenance_tasks.donor_options(bad, False)
    with pytest.raises(NetworkError):
        maintenance_tasks.donor_options(1, "yes")
