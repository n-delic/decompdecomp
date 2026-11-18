"""The app keeps idle connections open longer than the gateway pools them (client error (SendRequest))."""
import re
from pathlib import Path

from decompnet.app import cli

ROOT = Path(__file__).resolve().parents[1]


def test_app_keep_alive_outlasts_every_gateway_connection_pool():
    """With uvicorn's 5 s default the app closed connections the gateway's 30 s pools still reused, and a click sent
    on one as it closed failed with "decompdecomp isn't answering (client error (SendRequest))"."""
    pools = [int(seconds) for name in ('window.rs', 'main.rs')
             for seconds in re.findall(r'pool_idle_timeout\(Duration::from_secs\((\d+)\)\)',
                                       (ROOT / 'gateway' / 'src' / name).read_text(encoding='utf-8'))]
    assert pools, 'the gateway names its connection pools idle timeouts'
    assert cli.server_config(lambda *a: None).timeout_keep_alive > max(pools)
    reader = (ROOT / 'src' / 'decompnet' / 'app' / 'tool_reader.py').read_text(encoding='utf-8')
    keep = int(re.search(r'timeout_keep_alive=(\d+)', reader).group(1))
    assert keep > max(pools)
