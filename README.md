<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
    <img alt="Linux | Windows" src="https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20-15140f?style=flat-square">
  <a href="https://discord.gg/hhEm3zZagB"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white"></a>
  <img alt="Agents: Claude Code, Codex, opencode, Antigravity, any API model" src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20opencode%20%C2%B7%20API%20models-157a49?style=flat-square">
</p>

decompdecomp is an app that decompiles games you own. AI agents write C or C++ for each function of the game. The
game's original compiler builds it. When the result is the same as the game, byte for byte, the function is
matched. In the end you get a project that builds your game again, exactly.

**New in 1.0.3-alpha: much faster, in its own window.** The app is ready the moment it opens (3.7 s before, 15 ms
now). The map of a big game updates ten times faster after a match. With 16 agents on two games, the app answers in
under a tenth of a second (1.4 s before), and agents restart by themselves after a crash or a usage limit.

<p align="center">
  <img alt="decompdecomp on Demo Quest 64: the autopilot matches 20 functions for free, then a helper matches six more, each judged byte for byte." src=".github/assets/demo.webp" width="900">
  <br>
  <sub>The app on Demo Quest 64, the demo ROM that comes with it. To keep the recording short, the helper is a
  stand-in for a model that hands in the demo's own C; the app compiles and judges every function for real.</sub>
</p>

## Install

decompdecomp runs on **Linux** and **Windows (via WSL)**, on a 64-bit Intel or AMD PC. Install it with one command. The installer also gets
the free compilers and keeps decompdecomp up to date:

```
curl -fsSL https://decomp.ndelic.com/install.sh | bash
```

Or install a package from the [releases](https://github.com/n-delic/decompdecomp/releases):

```
sudo apt install ./decompdecomp_*_amd64.deb        # Debian, Ubuntu, Pop!_OS, Mint
sudo dnf install ./decompdecomp-*.x86_64.rpm       # Fedora
chmod +x decompdecomp-*-x86_64.AppImage && ./decompdecomp-*-x86_64.AppImage   # any other distribution
```

macOS isn't supported (yet).

## Start

Type `decomp`, or open decompdecomp from your apps menu. It opens in a window of its own.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open
   Demo Quest 64.
2. **Start agents.** Any model with an API key (DeepSeek, OpenRouter, OpenAI, a local model), or Claude Code,
   Codex, opencode and Antigravity CLI. Run several, and different models: they work on different functions at the
   same time.
3. **Watch the map.** Each square is a function. It turns green when its function matches. The autopilot matches
   the easy ones for free, before any agent starts.
4. **Export** (in the project's header). You get a decomp project that builds your game again, byte for byte.

You can also continue a community decomp: paste the address of its repository and pick your own copy of its game.

When you close the window while agents work, decompdecomp asks you: stop them and quit, or keep them working.
**Quit** ends the app and everything it started.

## Coming from an older version (1.0.x)

Your projects stay. The new version opens the same `~/Decomp` folder, with every match, name and note in it.

1. **Install the new version** the same way as before: the install command above, or the new `.deb`, `.rpm` or
   AppImage. The installer removes an older `.deb` or `.rpm` of decompdecomp, so your apps menu shows one. Going
   the other way, from the install command to a package? Remove the old copy first: `uv tool uninstall decompnet`.
2. **Start it** with `decomp` or from your apps menu. It opens in its own window now, not in your browser.
3. **Start your agents again.** Helper agents that were working in the old version don't restart by themselves the
   first time. Open each project and start them; from then on they restart by themselves.

What changed on the way:

- **Agents run inside the app only.** `decomp connect` and `decomp mcp` are gone. On its first start, the new
  version removes the old `decomp` entries from your agents' settings (Claude Code, Codex, opencode, Antigravity and
  others), and its home page says what it removed. Before that, those entries started the old app again whenever
  you closed it.
- **The Source tab is now the Export button** in the project's header.

## Moving to another computer

decompdecomp saves the work on each N64 and PlayStation 2 project as plain files in the project's `progress/`
folder, every few minutes: matched C, best attempts, headers, names, notes and settings. It never contains the game,
so you can keep it in a git repository. GameCube, Wii and PC projects and community decomps are git repositories
already: push them.

The simplest way is to copy your whole `~/Decomp` folder to the same place on the new computer. Copy the hidden
`.decomp` folder in it too: it holds the matches of every project. decompdecomp then finds every project. To take a
single N64 or PlayStation 2 project, or to keep it in git:

**Share or commit only `progress/`.** The rest of a project's folder is made from your copy of the game, and the
game's code is its rights holder's: keep it to yourself.

```
~/Decomp/<project>/
├── progress/         your work: share this, commit this
│   ├── src/              each matched function's C
│   ├── best/             the closest attempt at each function that doesn't match yet
│   ├── include/          the shared headers every function is compiled with
│   ├── decompdecomp.json the game's fingerprint (never the game), compiler, layout, names, notes, settings
│   └── README.md         what this is and how to continue
├── rom.z64           your copy of the game: never share it
├── asm/  assets/     taken out of the game: never share them
└── src/  include/  *.ld  *.yaml  *.txt   the build decompdecomp makes from the game, again on any computer
```

1. Copy the project's `progress/` folder to the new computer, or push it to a git repository.
2. On the new computer, in decompdecomp: **Open a saved project**. Pick the folder (or paste the repository's
   address) and your own copy of the game.
3. decompdecomp checks it's the same game, rebuilds the project, and judges every saved function again.

## Platforms

| Platform | Status |
| :-- | :-- |
| Nintendo 64 | Recommended |
| PlayStation 2 | Beta |
| GameCube, Wii | Beta |
| Windows PC games | Beta |
| Nintendo DS | Beta |
| PlayStation, Game Boy Advance, 3DS, PSP, Switch, Xbox, Xbox 360 | Community decomps only |

1.0.3 rewrote most of the app, so every platform needs testing again, Nintendo 64 too. Try it on a game you own and
[tell us how it went](https://github.com/n-delic/decompdecomp/issues): what worked, what didn't, what was confusing. Reports are how a platform goes
from beta to recommended.

Proprietary compilers (CodeWarrior, Visual C++, IDO) are installed only after you accept their licenses in the app.

## Questions people ask

**Is this just Ghidra?** No. Ghidra (and IDA) show you C so you can *read* a program. That C usually doesn't compile,
and when it does, the game's compiler makes different machine code from it. decompdecomp keeps a function only when
its C, built by the game's own compiler, gives exactly the original bytes. That's what lets the result build the
game again, the base for ports and mods.

**What does the C look like?** At first, raw. A match proves the code is right, not that it's easy to read. Here is
one function of Demo Quest 64 as the autopilot matched it, and the same function made readable. Both compile to
the same bytes:

```c
/* As matched */
void func_80000A0C(s32 arg0) {
    s32 temp_v0;

    temp_v0 = D_8004AE30 + (arg0 * (D_8004AE58 + 1));
    D_8004AE30 = temp_v0;
    if (temp_v0 > 0xF423F) {
        D_8004AE30 = 0xF423F;
    }
}
```

```c
/* Made readable: adds points for a hit, times the combo, up to the highest score. */
void add_score(s32 points) {
    g_score += points * (g_player.combo + 1);
    if (g_score > MAX_SCORE) {
        g_score = MAX_SCORE;
    }
}
```

Agents do that second step too: once half a game matches, **Start agents** offers **Polish matched code**. Helpers
name functions, globals and parameters, describe structs in the shared header and add a comment, and a change is
kept only when the function still matches byte for byte. Names are the agent's reading of the code: the bytes
prove what a function does, not what its author called it.

**Has a whole game been decompiled with it?** Not yet. Games get far (hundreds or thousands of matched functions),
and the export rebuilds the game byte for byte from matched C plus assembly for what isn't matched yet. A game at
100%, polished, is the goal, and the first one will be announced here.

## Help make it better

decompdecomp is built in the open, and it's meant to be easy to work on. Read [CONTRIBUTING.md](CONTRIBUTING.md)
first. A copy of decompdecomp that runs from a git checkout is a separate app, with its own folder, so it never
touches the projects of the decompdecomp you have installed.

- [The guide](docs/GUIDE.md): how it works, agents, teams, platforms and the export.
- [ROADMAP.md](ROADMAP.md): what's planned, and which feature works on which platform.
- [Issues](https://github.com/n-delic/decompdecomp/issues): problems and requests.
- [Discord](https://discord.gg/hhEm3zZagB): questions, progress on your games, and talking with the people building it.

## Use it responsibly

- Use only games that you own. decompdecomp contains no game code and supplies no ROMs.
- Everything runs on your computer. Nothing of your game is sent anywhere, except to the AI model you choose.
- Do not publish the code that it makes from a game. That code belongs to the rights holder of the game.
- Follow the rules of any project that you contribute to.

## Credits

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf) by
[Sam Wolfs (@i12bp8)](https://github.com/i12bp8), who made it up to version 0.10.6 and then archived it. Thank you,
Sam. decompdecomp is not related to decomp.wtf, and its author does not endorse it.

decompdecomp uses splat, spimdisasm, objdiff, decomp-toolkit, m2c, Ghidra, decomp-permuter, wibo and the decompals
compilers. The
[guide](docs/GUIDE.md#built-on) lists them.

## License

decompdecomp is free software under the [GNU Affero General Public License version 3](LICENSE) © 2026 NDelic.
Anyone who gives or sells a copy of it, changed or not, must give its full source under the same license. So must
anyone who runs a changed copy for others to use over a network, a hosted room for example.

The code that came from decomp.wtf was released under the MIT license by Sam Wolfs; its notice is in
[LICENSE-decomp.wtf](LICENSE-decomp.wtf).

<p align="center"><sub>Not related to Nintendo, Sony or Microsoft. Platform names are trademarks of their owners.</sub></p>
