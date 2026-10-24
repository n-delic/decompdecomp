<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
  <img alt="Linux" src="https://img.shields.io/badge/platform-Linux-15140f?style=flat-square">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-15140f?style=flat-square">
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

decompdecomp runs on **Linux and Windows 10/11**, on a 64-bit Intel or AMD PC. On Linux, install it with one command. The installer also gets
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

On **Windows**, download and open `decompdecomp-<version>-windows-x64-setup.exe` from the
[releases](https://github.com/n-delic/decompdecomp/releases). Setup installs the Microsoft Edge WebView2 Runtime and
.NET Framework 4.8 when they are missing, then opens the app installer. It may ask for administrator permission and
needs an internet connection to download missing Microsoft components. The app installer lets you choose where
decompdecomp goes and whether to add a desktop shortcut (selected by default); it always adds a shortcut to Start.

To install from source with [uv](https://docs.astral.sh/uv/getting-started/installation/) instead, open a new
PowerShell window and run:

```powershell
uv tool install --python 3.12 "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/heads/main.tar.gz"
decomp setup
decomp shortcut
decomp
```

The app opens in a native Windows window. Its shortcuts appear on the desktop and in Start, and its tray icon
brings back a window closed while helpers work. Free tools use Windows builds where available. Projects stay in your Windows `Decomp` folder.

> [!IMPORTANT]
> **PS1, PS2 and most N64 games need WSL 2 on Windows.** Their period compilers (GCC for PlayStation, EE-GCC and
> CodeWarrior for PS2, KMC GCC for N64) exist only for Linux, so decompdecomp runs them inside WSL 2. Without it,
> every compile of such a game fails. Set it up before you import one: run `wsl --install` in an administrator
> PowerShell, restart if asked, then open Ubuntu once to finish its setup.
>
> GameCube, Wii, DS and Windows PC games, and N64 games built with IDO, don't need WSL: their compilers run on
> Windows itself.
macOS isn't supported in this version.

## Start

Type `decomp`, or open decompdecomp from your apps menu. It opens in a window of its own.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open
   Demo Quest 64. For an N64, PlayStation or PlayStation 2 game, the app offers to install all of that console's
   compilers first. Then it tests which one built the game. Tick the box only if you accept the proprietary ones.
2. **Start agents.** Any model with an API key (DeepSeek, OpenRouter, OpenAI, a local model), or Claude Code,
   Codex, opencode and Antigravity CLI. Run several, and different models: they work on different functions at the
   same time.
3. **Watch the map.** Each square is a function. It turns green when its function matches. The autopilot matches
   the easy ones for free, before any agent starts.
4. **Export** (in the project's header). You get a decomp project that builds your game again, byte for byte.

You can also continue a community decomp: paste the address of its repository and pick your own copy of its game.

To decompile with friends, open a project's **Decompile together** panel to host a room, or choose **Join a team's
room** from **New project**. If you've already imported that exact game, joining also shares your saved matches that
the host hasn't matched yet; the host verifies each one. For ROM projects, better unfinished attempts are shared too
when both projects use the same compiler settings. Everyone needs their own copy of the game.

When you close the window while agents work, decompdecomp asks you: stop them and quit, or keep them working.
**Quit** ends the app and everything it started.

## Coming from decomp.wtf or an older version (1.0.x)

Your projects stay, decomp.wtf's too: decompdecomp continues decomp.wtf and reads the same folder and database. On
Linux, the new version opens the same `~/Decomp` folder, with every match, name and note in it.
The old Windows installer kept its projects inside a WSL distribution. The Windows app now runs natively and uses
`%USERPROFILE%\Decomp`; it uses WSL for some Linux-only compilers, but doesn't scan WSL for projects automatically.

To move projects from the old Windows install, close its app, then copy the whole `Decomp` folder from
`\\wsl.localhost\decomp-wtf\home\decomp\Decomp` to `%USERPROFILE%\Decomp`. Include the hidden `.decomp` folder
inside it: that holds the project database and saved matches. The app reconnects projects to their copied folders.
Did you keep a project outside the `Decomp` folder, in `Documents` for example? Move that project's folder into
`%USERPROFILE%\Decomp` too, and keep its folder name: the app finds a moved project by its name there.

**Open a saved project** is for something else: a `progress/` folder that decompdecomp saved, with a
`decompdecomp.json` in it. Use it if you only have a project's `progress/` folder, with your own copy of the game.
A project's own folder isn't a save, and neither is its `.decomp.wtf.json`: that file says how the project builds.
Keep that file's name as it is. decompdecomp still reads it as `.decomp.wtf.json`.

1. **Install the new version** the same way as before: the install command above or the latest `.deb`, `.rpm`,
   AppImage or Windows setup bundle. The installer removes an older `.deb` or `.rpm` of decompdecomp, so your apps menu shows
   one. Going the other way, from the install command to a package? Remove the old copy first: `uv tool uninstall decompnet`.
2. **Start it** with `decomp` or from your apps menu. It opens in its own window now, not in your browser.
3. **Start your agents again.** Helper agents that were working in the old version don't restart by themselves the
   first time. Open each project and start them; from then on they restart by themselves.

What changed on the way:

- **Agents run inside the app only.** `decomp connect` and `decomp mcp` are gone. On its first start, the new
  version removes the old `decomp` entries from your agents' settings (Claude Code, Codex, opencode, Antigravity and
  others), and its home page says what it removed. Before that, those entries started the old app again whenever
  you closed it.
- **The Source tab is now the Export button** in the project's header.
- **Windows projects and compilers.** The app server and window run natively on Windows. Linux-only period compilers
  run through WSL 2, using temporary Linux-side build stages so old 32-bit tools work. Run `decomp shortcut` once to
  put native shortcuts on the Windows desktop and in Start. Setup installs WebView2 and .NET Framework 4.8 when missing;
  pywebview is installed with the app.

## Moving to another computer

decompdecomp saves the work on each N64, PlayStation and PlayStation 2 project as plain files in the project's `progress/`
folder, every few minutes: matched C, best attempts, headers, names, notes and settings. It never contains the game,
so you can keep it in a git repository. GameCube, Wii and PC projects and community decomps are git repositories
already: push them.

The simplest way is to copy your whole `~/Decomp` folder to the same place on the new computer. Copy the hidden
`.decomp` folder in it too: it holds the matches of every project. decompdecomp then finds every project. To take a
single N64, PlayStation or PlayStation 2 project, or to keep it in git:

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
| PlayStation (PS1) | Beta |
| PlayStation 2 | Beta |
| GameCube, Wii | Beta |
| Windows PC games | Beta |
| Nintendo DS | Beta |
| Game Boy Advance, 3DS, PSP, Switch, Xbox, Xbox 360 | Community decomps only |

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

**What do I end up with?** The source code of a game you own, that builds it again. A DS game started from scratch
reached 2,400 matched functions (58% of its code) in under two days, about half of them matched by free models, and
its project built a game that boots in an emulator: matched functions from their C, the rest from the original's code
until agents get to it. It isn't overnight: the last functions are the big ones, and they take time and tokens. But
every match stays matched, and each one makes the project more of what a game's source is: something you can read,
change, port and mod.

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
