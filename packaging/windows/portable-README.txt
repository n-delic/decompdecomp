decompdecomp portable for Windows x64

Extract the entire ZIP, then double-click decompdecomp.exe. Keep the python and
packages folders beside the EXE. No app installation or administrator access is
needed, and no shortcuts or uninstall entry are created automatically.

Projects and settings use %USERPROFILE%\Decomp-RetroReversing, separate from
upstream decompdecomp. An explicit DECOMP_HOME environment variable still works.
To move your work to another computer, copy that data folder as well as the app.

The native window needs .NET Framework 4.8 and Microsoft Edge WebView2 Runtime.
Windows Update normally provides .NET. If WebView2 is missing, install Microsoft's
runtime from https://developer.microsoft.com/microsoft-edge/webview2/ .
The setup download can install these prerequisites for you; the portable app
does not install them silently. Compilers are selected separately in Setup.
