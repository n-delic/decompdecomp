<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
  <img alt="Linux" src="https://img.shields.io/badge/platform-Linux-15140f?style=flat-square">
  <img alt="Agents: Claude Code, Codex, opencode, Antigravity, any API model" src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20opencode%20%C2%B7%20API%20models-157a49?style=flat-square">
</p>

decompdecomp is an app that decompiles games you own. AI agents write C or C++ for each function of the game. The
game's original compiler builds it. When the result is the same as the game, byte for byte, the function is
matched. In the end you get a project that builds your game again, exactly.

> **The app is being rewritten.** This branch is the new version: its own window, agents that restart by themselves,
> and a faster app. It isn't released yet. [docs/REWRITE.md](docs/REWRITE.md) says what changes and why.

<p align="center">
  <img alt="decompdecomp on the demo ROM: the autopilot matches functions for free, then an agent matches more, one function at a time." src=".github/assets/demo.webp" width="900">
  <br>
  <sub>The app on Demo Quest 64, the demo ROM that comes with it.</sub>
</p>

## Install

decompdecomp runs on **Linux**, on a 64-bit Intel or AMD PC. Install it with one command. The installer also gets
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

Windows and macOS aren't supported in this version.

## Start

Type `decomp`, or open decompdecomp from your apps menu. It opens in a window of its own.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open
   Demo Quest 64.
2. **Start agents.** Claude Code, Codex, opencode, Antigravity CLI, or any model with an API key (DeepSeek,
   OpenRouter, OpenAI, a local model). Run several, and different models: they work on different functions at the
   same time.
3. **Watch the map.** Each square is a function. It turns green when its function matches. The autopilot matches
   the easy ones for free, before any agent starts.
4. **Export.** You get a decomp project that builds your game again, byte for byte.

You can also continue a community decomp: paste the address of its repository and pick your own copy of its game.

When you close the window while agents work, decompdecomp asks you: stop them and quit, or keep them working.
**Quit** ends the app and everything it started.

## Platforms

| Platform | Status |
| :-- | :-- |
| PlayStation 2 | Works: tested on real games, with several compilers per game |
| Nintendo 64 | Works: tested on real games |
| GameCube, Wii | Works, but not yet tested from start to end |
| Windows PC games | Beta |
| 3DS, PSP, Switch, Xbox, Xbox 360 | Community decomps only |

Proprietary compilers (CodeWarrior, Visual C++, IDO) are installed only after you accept their licenses in the app.

## Help make it better

decompdecomp is built in the open, and it's meant to be easy to work on. Read [CONTRIBUTING.md](CONTRIBUTING.md)
first. A copy of decompdecomp that runs from a git checkout is a separate app, with its own folder, so it never
touches the projects of the decompdecomp you have installed.

- [The guide](docs/GUIDE.md): how it works, agents, teams, platforms and the export.
- [ROADMAP.md](ROADMAP.md): what's planned, and which feature works on which platform.
- [Issues](https://github.com/n-delic/decompdecomp/issues): problems and requests.

## Use it responsibly

- Use only games that you own. decompdecomp contains no game code and supplies no ROMs.
- Everything runs on your computer. Nothing of your game is sent anywhere, except to the AI model you choose.
- Do not publish the code that it makes from a game. That code belongs to the rights holder of the game.
- Follow the rules of any project that you contribute to.

## Credits

decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf) by
[Sam Wolfs (@i12bp8)](https://github.com/i12bp8), who made it up to version 0.10.6 and then archived it. Thank you,
Sam. decompdecomp is not related to decomp.wtf, and its author does not endorse it.

decompdecomp uses splat, spimdisasm, objdiff, decomp-toolkit, m2c, Ghidra and the decompals compilers. The
[guide](docs/GUIDE.md#built-on) lists them.

## License

decompdecomp is free software under the [GNU Affero General Public License version 3](LICENSE) © 2026 NDelic.
Anyone who gives or sells a copy of it, changed or not, must give its full source under the same license. So must
anyone who runs a changed copy for others to use over a network, a hosted room for example.

The code that came from decomp.wtf was released under the MIT license by Sam Wolfs; its notice is in
[LICENSE-decomp.wtf](LICENSE-decomp.wtf).

<p align="center"><sub>Not related to Nintendo, Sony or Microsoft. Platform names are trademarks of their owners.</sub></p>
