"""decompassets is a dependency (RetroReversing/decomp-assets, which tests the library itself); this checks how the
app uses it."""
import importlib

N64 = ("textures", "audio", "ptr_audio", "models", "model_links", "model_scenery", "archived_models", "texture_packs",
       "animation", "fonts", "resources", "resource_inspection", "native_scene")


def test_app_names_are_the_library_modules():
    """The app keeps its old names for the asset readers; both names must be one module (patches reach both)."""
    from decompassets import archives, formats, rnc
    from decompnet.consoles.n64 import rnc as n64_rnc
    from decompnet.rom import asset_archives, asset_formats
    assert (asset_formats, asset_archives, n64_rnc) == (formats, archives, rnc)
    for name in N64:
        assert importlib.import_module(f"decompnet.consoles.n64.{name}") is importlib.import_module(f"decompassets.n64.{name}")


def test_one_asset_error_for_every_caller():
    """ROM Explorer catches AssetError from rom.asset_formats; the library's recognisers raise the same class."""
    from decompassets.formats import AssetError
    from decompnet.rom.asset_formats import AssetError as AppAssetError
    assert AssetError is AppAssetError


def test_the_n64_console_gives_the_library_its_mips_reader(monkeypatch):
    """Audio banks find their sample tables from loader calls, which only the app can read."""
    from decompassets import n64
    from decompnet.arch import mips_assets
    from decompnet.consoles import get
    seen = {}
    monkeypatch.setattr(n64, "regions", lambda image, *, context=None, **kwargs: seen.update(context) or [])
    get("n64").asset_regions(b"", context={"gp": 4})
    assert seen == {"read_code": mips_assets.read, "gp": 4}


def test_the_library_never_imports_the_app():
    from pathlib import Path

    import decompassets
    for path in Path(decompassets.__file__).parent.rglob("*.py"):
        assert "decompnet" not in path.read_text(), path

