"""Every process of the app switches threads quickly: with the default 5 ms, a thread stepping through sqlite rows
waited a switch interval per row behind each busy thread (a 1,700-row read: 18 s of wall time for 0.04 s of CPU)."""
import subprocess
import sys


def interval(env_value=None):
    env = {'PATH': '/usr/bin:/bin'}
    if env_value is not None:
        env['DECOMP_SWITCH_INTERVAL'] = env_value
    out = subprocess.run([sys.executable, '-c', 'import sys, decompnet; print(sys.getswitchinterval())'],
                         capture_output=True, text=True, check=True, env=env,
                         cwd=__file__.rsplit('/tests/', 1)[0] + '/src').stdout
    return float(out)


def test_importing_the_package_shortens_the_switch_interval():
    assert interval() == 0.0005


def test_the_environment_overrides_it_and_zero_keeps_pythons_default():
    assert interval('0.001') == 0.001
    assert interval('0') == 0.005
    assert interval('nonsense') == 0.0005
