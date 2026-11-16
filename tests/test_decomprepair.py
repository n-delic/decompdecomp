"""The shared rewrite dependency preserves the app's imports and isolated generator launch."""
import importlib
from pathlib import Path

from decompnet.app import permute


def test_legacy_imports_are_the_shared_library_modules():
    for app, library in [('permuter', 'permuter'), ('permuter_gen', 'permuter_gen'),
                         ('app.permuter_local', 'permuter_local')]:
        assert importlib.import_module('decompnet.' + app) is importlib.import_module('decomprepair.' + library)


def test_generator_runs_beside_its_guards_in_the_tool_environment():
    from decomprepair import permuter_gen
    assert permute.GENERATOR == Path(permuter_gen.__file__).resolve()
    for helper in ('permuter_capture.py', 'permuter_dependencies.py', 'permuter_formatting.py'):
        assert permute.GENERATOR.with_name(helper).is_file()
