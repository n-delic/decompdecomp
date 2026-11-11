<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.svg">
    <img alt="decompdecomp" src=".github/assets/logo-light.svg" width="560">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="AGPL-3.0 license" src="https://img.shields.io/badge/license-AGPL--3.0-157a49?style=flat-square"></a>
  <img alt="Linux" src="https://img.shields.io/badge/platform-Linux-15140f?style=flat-square">
  <img alt="macOS" src="https://img.shields.io/badge/platform-macOS%20Intel%20%2F%20Apple%20Silicon-15140f?style=flat-square">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-15140f?style=flat-square">
  <a href="https://discord.gg/hhEm3zZagB"><img alt="Discord" src="https://img.shields.io/badge/Discord-join-5865F2?style=flat-square&logo=discord&logoColor=white"></a>
  <img alt="Agents: Claude Code, Codex, Cursor, Kimi, opencode, Antigravity, ZCode, Oh My Pi, any API model" src="https://img.shields.io/badge/agents-Claude%20Code%20%C2%B7%20Codex%20%C2%B7%20Cursor%20%C2%B7%20Kimi%20%C2%B7%20opencode%20%C2%B7%20Antigravity%20%C2%B7%20ZCode%20%C2%B7%20Oh%20My%20Pi%20%C2%B7%20API%20models-157a49?style=flat-square">
</p>

decompdecomp is an app that decompiles games you own. Helper agents write C or C++, the game's original compiler builds it, and a function is matched only when
the result is byte-identical to the game. The export builds your game again.

## Install
decompdecomp runs on Linux and Windows 10/11 on x86-64, and on macOS 12+ on Intel or Apple Silicon. The Linux installer also gets the free compilers
and keeps decompdecomp up to date.

### Linux
Run this command in a terminal:

```
curl -fsSL https://decomp.ndelic.com/install.sh | bash
```

Linux packages are also on the [latest release](https://github.com/n-delic/decompdecomp/releases/latest):

```
sudo apt install ./decompdecomp_*_amd64.deb        # Debian, Ubuntu, Pop!_OS, Mint
sudo dnf install ./decompdecomp-*.x86_64.rpm       # Fedora
chmod +x decompdecomp-*-x86_64.AppImage && ./decompdecomp-*-x86_64.AppImage   # any other distribution
```

### Windows
Download and open `decompdecomp-<version>-windows-x64-setup.exe` from the [latest release](https://github.com/n-delic/decompdecomp/releases/latest). Setup
installs WebView2 and .NET Framework 4.8 when they are missing.

> [!IMPORTANT]
> **PS1, PS2 and most N64 games need WSL 2 on Windows.** Their period compilers use Linux builds through WSL 2.
> Run `wsl --install` in an administrator PowerShell before you import one, then open Ubuntu once.
>
> GameCube, Wii, DS and Windows PC games, and N64 games built with IDO, do not need WSL 2.

To install from source with [uv](https://docs.astral.sh/uv/getting-started/installation/), open a new PowerShell window and run:

```powershell
uv tool install --python 3.12 "decompnet @ https://github.com/n-delic/decompdecomp/archive/refs/heads/main.tar.gz"
decomp setup
decomp shortcut
decomp
```

### macOS (Intel and Apple Silicon)
For an app in `/Applications`, download `macos_arm64` (Apple Silicon) or `macos_x86_64` (Intel) from the
[latest release](https://github.com/n-delic/decompdecomp/releases/latest). Unzip it, move `decompdecomp.app` to Applications. macOS may ask for approval on the first launch. Install the free compilers and tools from Setup.

To install the current source instead, run:

```sh
curl -fsSL https://decomp.ndelic.com/install.sh | bash -s -- --source branch:main
# Alternatively, install a local checkout:
git clone https://github.com/n-delic/decompdecomp.git
cd decompdecomp
bash site/public/install.sh
```

The installer creates `~/Applications/decompdecomp.app`, and projects stay in `~/Decomp`.

On Apple Silicon, install Rosetta before setup:

```sh
softwareupdate --install-rosetta
```

For PS2 Sony EE-GCC, run `brew install colima docker`, then choose **Prepare Linux runner** in Compilers & tools ([setup](docs/linux-compiler-vm.md)).
Linux-only PS1 compilers need a Linux computer. Xcode Command Line Tools (`xcode-select --install`) are needed for some Mac tools.

## Start
Type `decomp`, or open decompdecomp from your apps menu. It opens in its own window.

1. **Open a game.** Pick a ROM, a disc image or a program file of a game that you own. To try the app first, open Demo Quest 64. For an N64, PlayStation or
   PlayStation 2 game, the app offers to install that console's compilers first. Tick the box only if you accept the proprietary ones.
2. **Start helper agents.** Click **Start agents**. Choose any API model (DeepSeek, OpenRouter, OpenAI, or a local model), or a CLI helper agent: Claude Code,
   Codex, Cursor Agent, Kimi Code, opencode, Antigravity CLI, ZCode CLI or Oh My Pi. Run several; they work on different functions at the same time.
3. **Watch the map.** Each square is a function and turns green when it matches. The autopilot matches the easy functions at no cost before any helper agent
   starts.
4. **Export.** Use **Export** in the project header. You get a decomp project that builds your game again, byte for byte.

No whole game has been decompiled yet. The export uses matched C and assembly for the rest, so it still builds the game.

## Platforms

| Console | Status | Start from |
| :-- | :-- | :-- |
| Nintendo 64 | Recommended | ROM |
| PlayStation | Beta | Disc or PS-X EXE |
| PlayStation 2 | Beta | Disc or ELF |
| PlayStation 3 | Beta | Decrypted EBOOT.ELF |
| PlayStation 4 | Beta | Decrypted `eboot.elf` (Linux x86-64 only) |
| PSP | Beta | Unencrypted or decrypted UMD image, ELF or PRX |
| GameCube, Wii | Beta | Disc or DOL |
| Windows PC | Beta | 32-bit or 64-bit `.exe` |
| Xbox | Beta | `default.xbe`, `.xiso` or Redump `.iso` |
| Nintendo DS | Beta | ROM (`.nds`) |
| Game Boy Advance | Beta | ROM (`.gba`) |
| 3DS, Switch, Xbox 360 | Public decomps only | Community decomp fork |

Read [ROADMAP.md](ROADMAP.md#what-works-where) for each feature and console. Proprietary compilers (IDO, CodeWarrior, SN Systems, Visual C++) install only after
you accept their licenses in Setup. Visual C++ 2017 needs [wine](https://www.winehq.org/) on Linux and macOS; see [Visual C++ builds](docs/VISUAL-CPP.md).
PS4 projects need the Linux x86-64 app, including inside WSL 2; the native Windows and macOS apps cannot build them. They use the free pinned
Clang 5.0.2 pack and strict objdiff, tested on leaf functions and same-file, cross-file and import calls. decompdecomp includes no Sony SDK
and no game files. Many other features are still gaps; encrypted SELF, PKG and PRX modules are not supported.

## Other tasks
**Update.** It tells you when a new version is available; one click installs and restarts it. See [Installation details](docs/GUIDE.md#installation-details).

**From decomp.wtf or an older 1.0.x version.** decompdecomp reads the same `~/Decomp` folder and database. On Windows, copy the old
`\\wsl.localhost\decomp-wtf\home\decomp\Decomp` folder to `%USERPROFILE%\Decomp`, with its hidden `.decomp` folder. Start your helper agents again after the
first start.

**Move to another computer.** Copy the whole `~/Decomp` folder, including the hidden `.decomp` folder. To move one project, copy its `progress/` folder or push
its git repository, then choose **Open a saved project** with your own copy of the game.

**Multiple Claude accounts.** In **Agents & models → Claude Code → Additional accounts**, click **Add account**. Give it a label, run the command shown, sign in
with `/login`, and choose the account when you start a helper agent.

**ROM library.** In **Settings → ROM library**, choose a folder with your extracted game files. Click **Search ROM library** on an imported project's setup page to fill in a
verified match.

## Use it responsibly
- Use only games that you own. decompdecomp contains no game code and supplies no ROMs.
- Everything runs on your computer. Nothing of your game is sent anywhere, except to the model you choose.
- Do not publish the code that decompdecomp makes from a game. That code belongs to the game's rights holder.
- Follow the rules of any project that you contribute to.

## Contributing
Read [CONTRIBUTING.md](CONTRIBUTING.md) first.

- [The guide](docs/GUIDE.md) and [ROADMAP.md](ROADMAP.md): how it works, what is planned, and which feature works on which console.
- [Issues](https://github.com/n-delic/decompdecomp/issues): problems and requests. [Discord](https://discord.gg/hhEm3zZagB): questions and progress.

## Credits
decompdecomp continues [decomp.wtf](https://github.com/i12bp8/decomp.wtf) by [Sam Wolfs (@i12bp8)](https://github.com/i12bp8), who made it up to version 0.10.6
and then archived it. Thank you, Sam. decompdecomp is not related to decomp.wtf, and its author does not endorse it.

decompdecomp uses splat, spimdisasm, objdiff, decomp-toolkit, m2c, Ghidra, decomp-permuter, wibo, wine and the decompals compilers. The
[guide](docs/GUIDE.md#built-on) lists them.

## License
decompdecomp is free software under the [GNU Affero General Public License version 3](LICENSE) © 2026 NDelic. Anyone who gives or sells a copy, changed or not,
must give its full source under the same license. So must anyone who runs a changed copy for others over a network.

The code from decomp.wtf was released under the MIT license by Sam Wolfs; its notice is in [LICENSE-decomp.wtf](LICENSE-decomp.wtf).

<p align="center"><sub>Not related to Nintendo, Sony or Microsoft. Platform names are trademarks of their owners.</sub></p>
