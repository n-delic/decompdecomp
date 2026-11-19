"""The map's colours, read from the window's own stylesheets (static/tokens.css, static/app.css) when the TUI starts,
so a change to the window's colours reaches the TUI with nothing to copy by hand. The window lets the browser turn
each CSS colour into RGB (static/js/map.js, cssColor); here it's done for the few forms the map's colours use: a
var(), an oklch(), and a color-mix(in oklch, ...) of two of them.
"""

from __future__ import annotations

import math
import re

from ..support import static_file

# a map status and the window's CSS variable for its cell (static/js/map.js, readColors and cellColor)
STATUS_VARS = {"polished": "--polished", "matched": "--match", "in progress": "--hl", "tried": "--tried",
               "not C": "--not-c", "open": "--todo"}
# a text console (8 background colours, no shades): one basic colour each, as different from each other as can be
CONSOLE = {"polished": "cyan", "matched": "green", "in progress": "yellow", "tried": "blue", "not C": "red",
           "open": "white"}
DEFINITION = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")


def _blocks(css: str, selector: str) -> str:
    """The declarations of every rule whose selector is exactly `selector`, in order (a later one wins)."""
    out = []
    for at in re.finditer(r"(?:^|[}\s])" + re.escape(selector) + r"\s*\{", css):
        depth, start = 0, at.end()
        for i in range(start, len(css)):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            if depth < 0:
                out.append(css[start:i])
                break
    return "\n".join(out)


def variables(dark: bool) -> dict[str, str]:
    """Every custom property the map's colours can reach, for the light or the dark theme."""
    tokens = (static_file("tokens.css") or b"").decode()
    app = (static_file("app.css") or b"").decode()
    out: dict[str, str] = {}
    for block in [_blocks(tokens, ":root"), _blocks(tokens, '[data-theme="dark"]') if dark else "",
                  _blocks(app, ":root"), _blocks(app, ':root[data-theme="dark"]') if dark else ""]:
        out.update((name, re.sub(r"/\*.*?\*/", "", value).strip()) for name, value in DEFINITION.findall(block))
    return out


def _oklch(value: str, names: dict[str, str], depth: int = 0) -> tuple[float, float, float]:
    """A CSS colour as OKLCH (lightness 0-1, chroma, hue in degrees)."""
    value = value.strip()
    if depth > 20:
        raise ValueError("the stylesheet's variables refer to each other in a loop")
    if (m := re.fullmatch(r"var\((--[\w-]+)\)", value)):
        return _oklch(names[m[1]], names, depth + 1)
    if (m := re.fullmatch(r"oklch\(\s*([\d.]+%?)\s+([\d.]+)\s+([\d.]+)\s*\)", value)):
        lightness = float(m[1].rstrip("%")) / (100 if m[1].endswith("%") else 1)
        return lightness, float(m[2]), float(m[3])
    if (m := re.fullmatch(r"color-mix\(\s*in oklch\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*(.+?)\s*\)", value)):
        a, b, share = _oklch(m[1], names, depth + 1), _oklch(m[3], names, depth + 1), float(m[2]) / 100
        turn = (b[2] - a[2] + 180) % 360 - 180  # hues the shorter way round, as CSS does
        return (a[0] * share + b[0] * (1 - share), a[1] * share + b[1] * (1 - share),
                (b[2] - turn * share) % 360)
    raise ValueError(f"a colour the TUI can't read: {value}")


def _hex(lch: tuple[float, float, float]) -> str:
    """OKLCH to the sRGB a terminal takes (Björn Ottosson's OKLab matrices), clipped into its gamut."""
    lightness, chroma, hue = lch
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    linear = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
              -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
              -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)

    def encode(u: float) -> int:
        u = min(1.0, max(0.0, u))
        return round(255 * (12.92 * u if u <= 0.0031308 else 1.055 * u ** (1 / 2.4) - 0.055))

    return "#" + "".join(f"{encode(u):02x}" for u in linear)


def window_palette(dark: bool) -> dict[str, str]:
    """The window's map colours as RGB, light or dark (CONSOLE, should the stylesheets be unreadable)."""
    names = variables(dark)
    try:
        return {status: _hex(_oklch(f"var({var})", names)) for status, var in STATUS_VARS.items()}
    except (KeyError, ValueError):
        return dict(CONSOLE)
