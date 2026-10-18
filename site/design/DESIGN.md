# decompdecomp Design System — DESIGN.md

## 0. Status

| Field | Value |
|---|---|
| Version | 0.1.0 |
| Owner | NDelic |
| Last reviewed | 2026-10-16 |
| Applies to | marketing (decomp.ndelic.com) and the app (`src/decompnet/app/static`) |
| Market & audience | global · English · Latin · desktop first |
| Primary language | en |
| Surface mode | Persuade |
| **Expression register** | R3 Expressive — a hobby decision made by curiosity, a category (decomp.me, Binary Ninja, IDA, Ghidra) at R2 with screenshots, a new brand that must be remembered; no photography, so the R3 is typographic |
| **Interaction depth** | I3 Demonstrative — the top question ("does it work on my game?") has a different answer per platform |
| **Design language** | light;achromatic;grotesk;airy;square;type;asymmetric;choreographed;solid — departing on density, radius, imagery, layout |
| Mode | Standard |
| Token source | `tokens/primitives.json`, `tokens/semantic.json`, `tokens/semantic.dark.json`, `tokens/components.json` |
| Build | `python3 scripts/build_tokens.py tokens/*.json --out <tmp> --format css,flat-json`, then copy `tokens.css` to `public/` (`build/` is git-ignored) |

## 1. Foundations of intent

**The memorable thing:** your agent, your game: the AI you already use works on a game you own, on your own computer.

**Attributes / anti-attributes:**

| We are | We are not | Visual consequence |
|---|---|---|
| Proven | Magical | the real bytes of a real function are the image; scores are shown; no "AI-powered", no sparkles |
| Yours | Hosted | "your" in every claim; the action is the install command |
| Hands-on | Corporate | a printed listing: mono bytes, a highlighter stroke, square-ish corners, ink buttons, no gradients |

**Direction: "Marked listing"** — Swiss poster discipline (one dominant flush-left block, an asymmetric grid) × the
colour world of a program printout marked with a yellow highlighter × the density of a data sheet in the details.

**Design language** (`design-language.md` §4): light;achromatic;grotesk;airy;square;type;asymmetric;choreographed;solid
Market: light;achromatic;grotesk;tight;soft;photo;centered;choreographed;solid — from 3 pages: https://binary.ninja https://hex-rays.com/ida-pro https://ghidra-sre.org; by eye: https://decomp.me (dark;low;grotesk;medium;soft;product;centered;functional;solid; the fetch was refused). The `photo` the script measured is product screenshots on all four.
Splits: surface (light ×2, dark ×2 with decomp.me), chroma, density, radius, layout, motion, controls
Departs on: density (airy — R3: one idea per screen, the bytes and the claim at poster scale; the category's tight pages read as manuals) · radius (square — a printed listing has no rounded corners; the dd mark is built of squares) · imagery (type — the user ruled out the app's interface, and the category's screenshots are what make it interchangeable; the bytes are the content) · layout (asymmetric — a Müller-Brockmann poster block flush left with a margin note, instead of the centred claim).
Chroma stays the market's achromatic in the first two screens (one highlighter stroke only, principle 2); the brand's yellow owns section 4 as a full colour field.
Market opening: h-wide;panel-under;img-full · Opening: h-left;panel-none;img-full (the bytes are the full-width image; the heading sits flush left over them; no panel: the tool is section 2, not the first screen, because this is not a `product` language once the screenshot is gone)
Nearest calibration point: none of the six (closest: neo-brutalist's type-as-image, without its raw controls or system type). Measured after the build: light;achromatic;grotesk;airy;square;type;asymmetric;choreographed;solid — matches this declaration (the first declaration said saturated/medium/soft; the page measured otherwise and the declaration was corrected to what was built, not the reverse).

**References:**

| # | Source | Type | Taken | Serves |
|---|---|---|---|---|
| R1 | https://decomp.me | UI, category | the function as the unit of work, with a score next to it | the worked example's score ladder |
| R2 | https://binary.ninja | UI, category | one short claim and a direct action — not the screenshot | first viewport copy |
| R3 | Program listings marked up with highlighters (the 1970s–80s practice of reading code on fan-fold paper) | non-UI, practice | the yellow stroke as "this is what's being worked on" | brand colour, the mark device |
| R4 | Josef Müller-Brockmann, concert posters for the Tonhalle Zürich (1950s–70s) | non-UI, poster | one dominant typographic block on an asymmetric grid, flush left | first viewport composition |
| R5 | Texas Instruments component datasheets (pinout and block diagrams) | non-UI, print | a labelled block diagram with small caps labels and leader lines | the pipeline diagram in the picker |
| R6 | https://ghidra-sre.org | UI, category | plain honesty: names the tools, no claims it can't back | the platforms and "built on" copy |

**Alternative considered — "Clean listing" (R2)**: the same palette and type, the bytes as a 120 px-tall figure
beside a 72 px headline instead of a poster, no highlighter animation, the picker unchanged. Cheaper and calmer;
not chosen because without the poster block the page reads as one more dev-tool landing and the brand has nothing
to be remembered by.

**Imagery art direction:** subject: the real bytes and C of Demo Quest 64's `clamp_s32` (the project's own
public-domain demo) · people: none · light: flat print · treatment: mono type on paper, one highlighter stroke ·
crop: the hex runs off the right edge · banned: screenshots of the app, CRT scanlines, pixel-art sprites, controller
icons, glowing code, any byte of a commercial game.
Anchor type: type as image.
Photographs needed: no — the anchor is the function's bytes set as type.

**Register techniques used** (`expression-register.md` §7): display type > 72 px (the bytes, ~150 px); asymmetric grid;
overlapping type (the heading over the bytes); entrance choreography (the highlighter draws across the marked word,
once); one sticky sequence: none. Scroll-linked animation: none (not needed for the job).

**Signature interaction:** pick the platform your game is for → the pipeline redraws (the file it takes → what
splits it → the compiler that judges it → how a match is proven → what you get, plus the status: recommended or beta).
Declared as `data-nsd-interaction="platform-pipeline"`. Without JavaScript: every pipeline listed as
static rows (a `<details>` per platform, N64 open).
Result form: diagram — because the answer is "what happens to my file", a chain of named tools.
Placed: section 2 of 5, reached from the first viewport by "Will it work on my game?". The page ends on: the install
command and the GitHub link.

**Proposals to confirm:** none — everything stated is in the README and guide.

**Convergence overrides:** none (0 warnings).

**Design principles:**
1. *Show the proof, not the promise* — when copy and evidence compete, the evidence (bytes, scores, tool names) wins.
2. *One highlighter* — only one thing per viewport is marked yellow; a second mark is removed.
3. *Your words first* — when a sentence can say "your" or "our", it says "your".

## 2. Color

| Scale | Hue (°) | Notes |
|---|---|---|
| neutral | 96 | paper to ink, chroma 0.006–0.014 |
| neutral-dark | 96 | its own palette: dark paper, not an inversion |
| brand | 96 | highlighter yellow; 5 is the stroke and the field (oklch 0.9 0.175 96), 12 the olive ink of buttons |
| success | 150 | "matched" only |

`action.primary` = brand.12 (olive ink) with `text.on-action` = brand.5 (highlighter) in light; brand.5 with dark ink in dark. 60/30/10: paper 60, ink 30, highlighter 10 — except the one colour-field section, where the highlighter is the surface.
Contrast: `contrast.py --tokens` exit 0 (all text roles AA on every surface, both modes).

## 3. Typography

| Role | Family | Why | Weights |
|---|---|---|---|
| Display | Archivo, width 125 (expanded) | an engineered grotesque whose wide cuts read as a poster without a second family | 800 |
| Text / UI | Archivo, width 100 | the same family at text width: one voice | 400, 600 |
| Mono | IBM Plex Mono | the bytes and C; open counters that stay clear at 150 px and at 14 px | 400, 600 |

## 4–5. Spacing, motion

6 px rhythm (the listing's line grid): 0 3 6 9 12 18 24 30 36 48 60 72 96 120 144 192. Radius 2/3/4/6 (paper does
not round). Borders 1.5 / 3 px (the highlighter weight). Motion 90/140/220/360/620 ms; one entrance (the stroke draws
in 620 ms); reduced motion: the stroke is simply there.

## 10. Never list

- no screenshots of the app; no CRT, scanlines, pixel sprites, controllers
- no gradient, glow or blur; no rounded "card" grid
- no second highlighter per viewport
- no byte of a commercial game — only Demo Quest 64

## 11. Review of record

| Gate | Grade | One line |
|---|---|---|
| 1 Brief & register honoured | A | R3 typographic: the bytes at 136 px (9.7× body), a full yellow field, one entrance; I3 picker in section 2 |
| 3 Hierarchy & composition | A | one focal point per screen; the claim and the install action fit the first 1280×800 screen; margin note over the faded row |
| 5 Colour & contrast | A | `contrast.py --tokens` exit 0 both modes; `--pairs` exit 0; one highlighter per viewport |
| 6 Spacing, layout, responsive | A | no sideways scroll at 375; commands and C wrap or scroll inside their panes; phone poster wraps to put the marked word on its own line |
| 10 Accessibility (incl. JS-disabled pass) | A | native radios, announced `<output>`, visible focus, reduced motion; without JS every pipeline and both commands show |
| 13 Slop & originality | A | `slop_lint.py` A (one LOW: no sticky result — the 375 edit test shows the answer on screen under the buttons, so none was added) |

Scripts: `slop_lint.py` A · `contrast.py --tokens` 0 · `build_tokens.py --check` no warnings · `design_log.py check` 0 warnings (first entry) ·
`shoot.py --set 'input[value="ps2"]=on' --expect '#answer'` exit 0: answer visible at 375; dark render checked (the highlighted bytes were light-on-yellow in dark mode, fixed).
Anchor at full weight in the first viewport: the function's bytes, two rows, the second word struck through with the highlighter. Previous run compared: the current site (C, a template-flavoured hero over the old simulation) — this is more expressive on every axis.
Budget: Standard R3 + I3, target ~30 min, actual ~17 min; cut: the crit board had no earlier pages to compare against (first logged project).
Gate 14 (studio crit): Idea 4 (the bytes your agent matched, marked by a highlighter: only this product has them) · Memorability 4 (giant hex with a tilted yellow stroke) · Imagery & craft 4 (margin note, stroke, rail; the station boxes are still close to a generic stepper) · Type & rhythm 4 (expanded Archivo against Plex Mono, sections at different pace) · Completeness 5 (no placeholders) · Interaction 4 (the highlighter draws your file's route through the tools and the sentence carries into Start). **Total 25** — every axis ≥ 4 but one short of A+ (26).
Studio test: a studio would sign it; first change: give each station its own small drawn object (a ROM, a disc, a compiler flag) instead of numbered boxes. Remembered: the yellow-struck bytes — the brief's "your agent, your game" sits right under them. Boldness spent: the first screen's bytes. Removed: the "Yours" counter styling (unused).
Open items: the user decides v2 vs the current site; privacy.html and og.png move over from `site/public` when v2 replaces it; the app's own UI could adopt these tokens later.

## 12. The app

The app adopts this system rather than keeping its own: `static/tokens.css` is a copy of `site/public/tokens.css`
(rebuild both from `site/tokens`), and `app.css` maps its working names (`--bg`, `--ink`, `--match`, …) onto it, so
light, dark and the app's own theme switch come from the tokens. Surface mode Operate: R1 Utility in the work views,
R2 on the home screen. What carries over from the site:

- **The highlighter means "being worked on"**: working squares on the ROM map, the working state and badge, the agent
  dot, the "agents working" pill, the active navigation item, the text selection. Always black on yellow.
- **Green means "matched"** and nothing else: progress, ✓, matched badges, recommended platforms.
- **Ink buttons with highlighter text** (yellow buttons with black text in dark), near-square corners (3–4 px).
- **Expanded Archivo** for page titles, the project name and the overview's numbers; Plex Mono for addresses and code.
- **Rules instead of cards** where the content is a row of facts (the overview's numbers); hairline panels elsewhere.
- Removed as slop: the frosted header, pulsing status dots, icon tiles on the home cards, coloured left borders.

Review: `slop_lint.py app.css index.html` C → B. What remains is chosen: the spinners are real loading
indicators, and the label/value tables are the data of a tool used daily (`density: tight` is native to Operate).
Checked by rendering every main screen in light and dark (home, overview, functions, agents, project settings,
agents & models, compilers & tools, settings, the export, Decompile together). The ROM map's colours are now
read through a canvas, so it draws any CSS colour the tokens use (it read OKLCH as RGB and drew blue).
