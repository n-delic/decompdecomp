# Contributing to decompdecomp

Thanks for helping. Bug reports, fixes, new platforms and better guides for agents are all welcome.

## The one rule: no game data

Never put any part of a game in an issue, a pull request, a test or a screenshot: no ROMs, disc images or
executables, and no code, assembly or assets taken from them. That includes the C your agents matched. It comes from
the game, and it belongs to you and the game's owner, not to this repository. Sam's request from decomp.wtf still
stands: only use decompdecomp on games you own, and respect the rules of the decomp projects you work with.

Before you attach a log (`~/Decomp/.decomp/logs/decomp.log`), read it, and cut out any code or assembly.

## Reporting a bug

[Open an issue](https://github.com/n-delic/decompdecomp/issues) with:

- your version (bottom left of the app, or `decomp update --check`) and your Linux distribution;
- the platform (N64, PS2, GameCube, PC, ...) and the compiler the project uses;
- what you did, what you expected, and what happened instead, with the error text;
- the part of the log around it, if it helps (see above).

The name of the game is fine to mention. Many bugs only show up on one game, and the name helps a lot.

## Working on the code

decompdecomp is Python 3.12+, managed with [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/n-delic/decompdecomp && cd decompdecomp
uv sync
uv run decomp                 # the app, in your browser (projects in ~/Decomp, or $DECOMP_HOME)
uv run pytest -q              # the tests
uv run pytest tests/test_ps2.py -q   # one file
uv run ruff check src tests projects   # the linter
```

If you use decompdecomp yourself, keep your own copy out of your tests: run the checkout with
`DECOMP_HOME=/some/scratch/folder uv run decomp`. A newer version that finds your installed app running replaces it.
The tests already use a folder of their own.

Compilers and tools (splat, objdiff, dtk, wibo, Ghidra and the period compilers) are pinned with checksums in
`src/decompnet/toolchains.toml`, and the app installs them in Setup. Many tests skip when a compiler isn't installed,
and that's fine. The homemade demo games in `projects/demo-*` are built by their own scripts. The N64 and PS1 demos
also need `ld.lld`, `llvm-objcopy` and `llvm-nm` on your PATH (Debian and Ubuntu: `apt install lld llvm`).

**[docs/CONVENTIONS.md](docs/CONVENTIONS.md) has the house rules, and [AGENTS.md](AGENTS.md) how the code is laid
out. Read them first.** In short:

- Match the code around you: plain-English docstrings and comments that say *why*.
- Every change in behaviour comes with a test in `tests/`. A test that needs a real game or a proprietary compiler
  skips when it isn't there; use or extend a demo game instead.
- Don't add a dependency for a few lines. capstone, pyelftools and httpx are already there.
- Errors shown to users or agents are sentences they can act on.
- `uv run ruff check src tests projects` passes. There's no formatter: don't reformat code you didn't change.

Some things we won't merge:

- Anything that weakens the judge. A match means the bytes are identical after linking (ROM mode), or objdiff
  says 100% (fork mode).
- Downloading a proprietary compiler without the user agreeing to it in Setup, or taking tools from PATH instead of
  the pinned ones.
- Letting agents edit a project's build setup. They're limited to source and header files on purpose.
- Links to the decomp.wtf website. That domain isn't ours. Keep the internal names (`decomp`, `decompnet`,
  `~/Decomp`, `.decomp.wtf.json`): existing installs depend on them.

Not sure what to work on? [ROADMAP.md](ROADMAP.md) lists what's planned and why, and has a table of which feature
works on which platform: every — in it is a gap to fill.

## Pull requests

- Keep each one to one change, and say in the description what it fixes or adds, and how you tested it.
- Commit messages describe what users get, in one sentence, with a body explaining why.
- Don't bump the version. Releases are made separately.
- Pull requests written with an AI agent are fine (AGENTS.md is there for it). Read the diff yourself, and make sure
  the tests pass.

By contributing you agree that your work is released under the project's [MIT license](LICENSE).

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf), which is archived. Please don't open
issues there.
