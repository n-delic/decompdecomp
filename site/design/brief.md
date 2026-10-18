# Design Brief — decompdecomp website (decomp.ndelic.com)

## 1. The product in one breath

| Field | Answer |
|---|---|
| What it is | A free, open-source app in which AI helper agents decompile a game the user owns into C that the game's original compiler turns into the same bytes. |
| Who it is for | Decomp and modding hobbyists, and developers curious about AI agents; on a desktop or laptop, usually in the evening; they arrive from GitHub, Reddit or Discord links. |
| The job they hire it for | When I own a game I want to mod or port, I want AI agents to turn its code back into real C, so I can change it without starting from assembly. |
| Business goal for this piece of work | Installs (the install command copied) and trust (the visitor understands that the match is proven, and that it runs locally). |
| Market & audience locale | Global · English · Latin script · desktop first (the app runs on Linux), phones for link previews · conventions: a copyable install command, a GitHub link, no sign-up. |
| Primary language | en |
| Platform(s) | Web marketing page, static files (Cloudflare or any static host). |
| Expression register | R3 Expressive — the visitor decides by curiosity and taste (a hobby tool, not a purchase), the category (Binary Ninja, IDA, Ghidra, decomp.me) sits at R2 with screenshots, and a new brand has to be remembered. Asset budget: no photography; a typographic and diagrammatic direction carries R3. |
| Interaction depth | I3 — signature interaction: "Will it work on my game?": pick the platform of the game → the pipeline for that platform redraws (the file it takes, the splitter, the compiler, the judge, what you get, and the platform's status). |
| Design language | Depart on three named axes (chroma, imagery, layout); keep the category's grotesk, solid controls and choreographed motion. |
| Mode | Standard |
| Surface type | landing / marketing |
| Scope of this engagement | redesign (new brand system; the app may follow) |

## 2. The memorable thing

Your game, checked byte for byte: AI agents work on a game you own, on your own computer, and the game's own compiler judges them.

## 3. Constraints

- **Design system:** none kept. Completely new (the user's answer); the "dd" pixel mark may change colour.
- **Visual assets available:** the dd mark; Demo Quest 64, the project's own public-domain demo ROM (its real bytes and C may be shown). No photography. No screenshots of the app (the user's instruction).
- **Fixed brand assets:** the name decompdecomp; the dd mark's shape.
- **Tech stack:** plain HTML/CSS/JS, self-hosted fonts (the site's CSP allows only its own origin).
- **Accessibility target:** WCAG 2.2 AA.
- **Localization:** English only.
- **Performance floor:** none special; must work without JavaScript.
- **Legal:** game code must never appear; only the demo's own bytes. Credit Sam Wolfs (decomp.wtf).
- **Fidelity:** production HTML for review beside the current site.

## 4. Users

| Proto-persona | Context of use | Top 3 tasks | Top frustration today | What "good" feels like |
|---|---|---|---|---|
| Modder with a childhood game | evening, desktop, came from a Reddit post | see if their console is supported; understand what it does; install | decomp projects take years and need assembly knowledge | "my agent can actually do this, and I can check it" |
| Developer trying AI agents | between tasks, already uses Claude Code / Codex or an API key | see which models it runs; trust the result | AI output that looks right and isn't | a hard, external judge of the AI's work |
| Existing decomper | knows splat/objdiff/decomp.me | see which tools and compilers are behind it; continue a community repo | tools that hide what they do | named tools, honest limits |

## 5. Competitive & reference landscape

| Product | What they do well | Where they fail users | Shared cliché |
|---|---|---|---|
| decomp.me | the scratch is the product; instant to try | says nothing to a newcomer | dark editor screenshot |
| Binary Ninja | one claim, two buttons, the product big | a carousel of screenshots, a feature-card grid | dark + screenshot |
| IDA Pro (hex-rays) | calm, credible, strong documentation links | generic lavender SaaS look | product screenshots in soft panels |
| Ghidra | utterly plain, trustworthy | no design at all | a wall of text links |

**Table stakes:** a copyable install, a GitHub link, the supported platforms and compilers named, honest limits.
**Deliberately broken:** the product screenshot as the first screen (the user's instruction, and every competitor does it); dark-by-default.

## 6. Brand attributes → visual consequences

| Attribute | Anti-attribute | Visual consequence |
|---|---|---|
| Proven | Magical | the real bytes of a real function are the image; "score 0" shown, never "AI-powered" |
| Yours | Hosted | the visitor's game and agents are named in the copy ("your"); the install is the action |
| Hands-on | Corporate | a printed-listing world: mono bytes, a highlighter, square corners, no gradients, no glow |

## 7. Success criteria

- Done when: a first-time visitor can say in five seconds what it does and that it runs on their computer; the platform picker answers "does it work on my game" in one click; slop_lint grade A/B; Gate 14 total reported honestly.

## 8. Open questions & assumptions

| # | Question | Assumption | Risk if wrong | Owner |
|---|---|---|---|---|
| 1 | Hosting | Cloudflare static assets, as the current site | none for the page | user |
| 2 | Logo colour | the dd mark in ink with the highlighter behind the second d | user prefers the green mark | user |
