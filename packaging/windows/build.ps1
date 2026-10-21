# Build the Windows setup bundle used by the release workflow. It installs missing Microsoft runtimes before running
# the app's MSI. WiX 3 is used because it does not require accepting the WiX 7 Open Source Maintenance Fee agreement.
[CmdletBinding()]
param([switch]$SkipInstallSmoke)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$version = (Select-String '^version = "(.*)"' (Join-Path $repoRoot 'pyproject.toml')).Matches.Groups[1].Value
$work = Join-Path ([IO.Path]::GetTempPath()) ("decompdecomp-build-" + [guid]::NewGuid())
$app = Join-Path $work 'app'
$wheelDir = Join-Path $work 'wheel'
$runtimeRequirements = Join-Path $work 'runtime-requirements.txt'
$dist = Join-Path $repoRoot 'dist'
New-Item -ItemType Directory -Force $app, $wheelDir, $dist | Out-Null
try {
    Push-Location $repoRoot
    try {
        uv export --locked --no-dev --no-emit-project --format requirements.txt --output-file $runtimeRequirements | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not export the locked runtime dependencies (exit code $LASTEXITCODE)" }
        uv build --wheel --out-dir $wheelDir | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "uv build failed with exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }

    # first, as packaging/linux/build.sh does: on a fresh runner uv's Python folder doesn't exist until something is
    # installed in it (v1.0.5-alpha's Windows bundle failed there)
    uv python install 3.12 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed with exit code $LASTEXITCODE" }
    $pythonDir = (uv python dir --managed-python).Trim()
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonDir)) { throw 'uv could not find its managed Python directory.' }
    $pythonHome = (Get-ChildItem -LiteralPath $pythonDir -Directory -Filter 'cpython-3.12.*-windows-x86_64-none' |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
    if (-not $pythonHome) { throw 'uv could not find its managed Python 3.12 installation.' }
    $python = Join-Path $pythonHome 'python.exe'
    $bundlePython = Join-Path $app 'python'
    New-Item -ItemType Directory -Force $bundlePython | Out-Null
    robocopy $pythonHome $bundlePython /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Could not copy the bundled Python runtime (robocopy exit code $LASTEXITCODE)" }
    $wheel = (Get-ChildItem -LiteralPath $wheelDir -Filter '*.whl' | Select-Object -First 1).FullName
    if (-not $wheel) { throw 'uv did not produce a decompdecomp wheel.' }
    $packages = Join-Path $app 'packages'
    New-Item -ItemType Directory -Force $packages | Out-Null
    uv pip install --python (Join-Path $bundlePython 'python.exe') --target $packages --requirements $runtimeRequirements
    if ($LASTEXITCODE -ne 0) { throw "uv pip install of locked runtime dependencies failed with exit code $LASTEXITCODE" }
    uv pip install --python (Join-Path $bundlePython 'python.exe') --target $packages --no-deps $wheel
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
    Copy-Item -LiteralPath (Get-Command uv).Source -Destination (Join-Path $app 'uv.exe') -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot 'src/decompnet/app/static/favicon.ico') -Destination (Join-Path $app 'decompdecomp.ico') -Force

    @'
import os
from pathlib import Path

root = Path(__file__).resolve().parent
os.environ["DECOMP_PACKAGE"] = "windows"
os.environ["PATH"] = os.pathsep.join((str(root), str(root / "packages" / "bin"), os.environ.get("PATH", "")))  # ninja
os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root / "packages"), os.environ.get("PYTHONPATH", ""))))
import site
site.addsitedir(str(root / "packages"))
from decompnet.app.cli import main

raise SystemExit(main())
'@ | Set-Content -LiteralPath (Join-Path $app 'decomp-launch.pyw') -Encoding utf8
    & (Join-Path $bundlePython 'python.exe') (Join-Path $app 'decomp-launch.pyw') --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The bundled app failed its launch smoke check.' }
    & (Join-Path $bundlePython 'python.exe') -c "import site,sys; site.addsitedir(sys.argv[1]); import decompnet.app.server" $packages
    if ($LASTEXITCODE -ne 0) { throw 'The bundled app failed its server startup import check.' }
    # picker.py uses Tk's native Windows folder/file dialogs, so keep tkinter and Tcl in the installed runtime.
    foreach ($unused in @('Lib/test', 'Lib/idlelib', 'Lib/site-packages', 'Lib/turtledemo',
                           'Lib/ensurepip', 'include', 'libs')) {
        $path = Join-Path $bundlePython $unused
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    Get-ChildItem -Path $app -Filter '__pycache__' -Directory -Recurse | Remove-Item -Recurse -Force

    $wixBin = $env:WIX_BIN
    if (-not $wixBin) {
        $wixZip = Join-Path $work 'wix314-binaries.zip'
        curl.exe -L --fail --silent --show-error `
            'https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip' -o $wixZip
        if ($LASTEXITCODE -ne 0) { throw "Could not download WiX Toolset 3.14.1 (curl exit code $LASTEXITCODE)" }
        $wixHash = (Get-FileHash -LiteralPath $wixZip -Algorithm SHA256).Hash
        if ($wixHash -ne '6AC824E1642D6F7277D0ED7EA09411A508F6116BA6FAE0AA5F2C7DAA2FF43D31') {
            throw "WiX Toolset archive checksum mismatch: $wixHash"
        }
        $wixBin = Join-Path $work 'wix'
        Expand-Archive -LiteralPath $wixZip -DestinationPath $wixBin
    }
    if (-not (Test-Path -LiteralPath (Join-Path $wixBin 'candle.exe'))) {
        throw 'WiX Toolset 3.14.1 is missing. Set WIX_BIN to its bin directory.'
    }
    $msiVersion = ($version -split '-')[0]
    $installer = Join-Path $dist "decompdecomp-$version-windows-x64.msi"
    $setup = Join-Path $dist "decompdecomp-$version-windows-x64-setup.exe"
    $harvest = Join-Path $work 'app-files.wxs'
    $objects = Join-Path $work 'wix-objects'
    $bundleObjects = Join-Path $work 'bundle-objects'
    $prereqs = Join-Path $work 'prerequisites'
    $licenseRtf = Join-Path $work 'LICENSE.rtf'
    $licenseText = Get-Content -LiteralPath (Join-Path $repoRoot 'LICENSE') -Raw -Encoding UTF8
    $rtf = [System.Text.StringBuilder]::new()
    [void]$rtf.Append('{\rtf1\ansi\deff0{\fonttbl{\f0\fnil\fcharset0 Arial;}}\viewkind4\uc1\pard\f0\fs18 ')
    foreach ($line in ($licenseText -split "`r?`n|`r")) {
        foreach ($character in $line.ToCharArray()) {
            $code = [int]$character
            if ($character -eq '\') { [void]$rtf.Append('\\') }
            elseif ($character -eq '{') { [void]$rtf.Append('\{') }
            elseif ($character -eq '}') { [void]$rtf.Append('\}') }
            elseif ($code -eq 9) { [void]$rtf.Append('\tab ') }
            elseif ($code -gt 127) {
                if ($code -gt 32767) { $code -= 65536 }
                [void]$rtf.Append("\u$code?")
            }
            else { [void]$rtf.Append($character) }
        }
        [void]$rtf.Append('\par' + "`r`n")
    }
    [void]$rtf.Append('}')
    [IO.File]::WriteAllText($licenseRtf, $rtf.ToString(), [Text.Encoding]::ASCII)

    New-Item -ItemType Directory -Force $prereqs, $bundleObjects | Out-Null
    $netFxInstaller = Join-Path $prereqs 'NDP48-Web.exe'
    $webView2Installer = Join-Path $prereqs 'MicrosoftEdgeWebView2Setup.exe'
    curl.exe -L --fail --silent --show-error 'https://go.microsoft.com/fwlink/?LinkId=2085155' -o $netFxInstaller
    if ($LASTEXITCODE -ne 0) { throw "Could not download Microsoft's .NET Framework 4.8 installer (curl exit code $LASTEXITCODE)" }
    curl.exe -L --fail --silent --show-error 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -o $webView2Installer
    if ($LASTEXITCODE -ne 0) { throw "Could not download Microsoft's WebView2 installer (curl exit code $LASTEXITCODE)" }
    foreach ($prerequisite in @($netFxInstaller, $webView2Installer)) {
        $signature = Get-AuthenticodeSignature -LiteralPath $prerequisite
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft*') {
            throw "The downloaded prerequisite is not signed by Microsoft: $prerequisite ($($signature.Status))"
        }
    }

    New-Item -ItemType Directory -Force $objects | Out-Null
    & (Join-Path $wixBin 'heat.exe') dir $app -cg AppFiles -dr INSTALLFOLDER -gg -sfrag -sreg -srd -var var.AppSource -out $harvest
    if ($LASTEXITCODE -ne 0) { throw "WiX Heat failed with exit code $LASTEXITCODE" }
    & (Join-Path $wixBin 'candle.exe') -arch x64 "-dAppVersion=$version" "-dMsiVersion=$msiVersion" `
        "-dAppSource=$app" "-dLicenseRtf=$licenseRtf" -out "$objects\" (Join-Path $PSScriptRoot 'installer.wxs') $harvest
    if ($LASTEXITCODE -ne 0) { throw "WiX Candle failed with exit code $LASTEXITCODE" }
    $wixObjects = (Get-ChildItem -LiteralPath $objects -Filter '*.wixobj' | ForEach-Object FullName)
    # ICE38, ICE64, ICE91: a per-user install's thousands of harvested files would each need a registry key path and
    # every folder a RemoveFolder entry; Windows Installer installs and removes them fine without (ICE64's cost: empty
    # folders may stay after an uninstall)
    $lightArgs = @('-out', $installer, '-sice:ICE38', '-sice:ICE64', '-sice:ICE91')
    if (-not $env:CI) { $lightArgs += '-sval' }  # the restricted local sandbox cannot reach the Windows Installer service for ICE validation
    & (Join-Path $wixBin 'light.exe') @lightArgs -ext WixUIExtension $wixObjects
    if ($LASTEXITCODE -ne 0) { throw "WiX Light failed with exit code $LASTEXITCODE" }

    $bundleObject = Join-Path $bundleObjects 'bundle.wixobj'
    & (Join-Path $wixBin 'candle.exe') -arch x64 -ext WixBalExtension -ext WixUtilExtension `
        "-dAppVersion=$version" "-dMsiVersion=$msiVersion" "-dAppMsi=$installer" `
        "-dLicenseRtf=$licenseRtf" "-dNetFxInstaller=$netFxInstaller" "-dWebView2Installer=$webView2Installer" `
        -out $bundleObject (Join-Path $PSScriptRoot 'bundle.wxs')
    if ($LASTEXITCODE -ne 0) { throw "WiX Candle failed to compile the Windows setup bundle (exit code $LASTEXITCODE)" }
    & (Join-Path $wixBin 'light.exe') -ext WixBalExtension -ext WixUtilExtension -out $setup $bundleObject
    if ($LASTEXITCODE -ne 0) { throw "WiX Light failed to link the Windows setup bundle (exit code $LASTEXITCODE)" }
    if ((Get-AuthenticodeSignature -LiteralPath $netFxInstaller).Status -ne 'Valid' -or
        (Get-AuthenticodeSignature -LiteralPath $webView2Installer).Status -ne 'Valid') {
        throw 'A Microsoft prerequisite signature became invalid while building the setup bundle.'
    }

    $msiApi = New-Object -ComObject WindowsInstaller.Installer
    $msiDb = $msiApi.OpenDatabase($installer, 0)
    function Test-MsiRow($database, [string]$query) {
        $view = $database.OpenView($query)
        $view.Execute()
        return $null -ne $view.Fetch()
    }
    foreach ($shortcut in @('StartMenuShortcutLink', 'DesktopShortcutLink')) {
        if (-not (Test-MsiRow $msiDb "SELECT ``Shortcut`` FROM ``Shortcut`` WHERE ``Shortcut``='$shortcut'")) {
            throw "The MSI is missing its $shortcut entry."
        }
    }
    if (-not (Test-MsiRow $msiDb "SELECT ``Dialog`` FROM ``Dialog`` WHERE ``Dialog``='InstallDirDlg'")) {
        throw 'The MSI is missing its install-location selection dialog.'
    }
    if (-not (Test-MsiRow $msiDb "SELECT ``Dialog`` FROM ``Dialog`` WHERE ``Dialog``='DesktopShortcutDlg'")) {
        throw 'The MSI is missing its desktop-shortcut choice dialog.'
    }
    if (-not (Test-MsiRow $msiDb "SELECT ``Control`` FROM ``Control`` WHERE ``Dialog_``='DesktopShortcutDlg' AND ``Control``='DesktopShortcutCheckBox' AND ``Property``='CREATE_DESKTOP_SHORTCUT'")) {
        throw 'The MSI desktop-shortcut dialog is missing its checkbox.'
    }
    if (-not (Test-MsiRow $msiDb "SELECT ``Argument`` FROM ``ControlEvent`` WHERE ``Dialog_``='InstallDirDlg' AND ``Control_``='Next' AND ``Event``='NewDialog' AND ``Argument``='DesktopShortcutDlg'")) {
        throw 'The MSI install flow does not show the desktop-shortcut choice.'
    }
    $desktopConditionView = $msiDb.OpenView("SELECT ``Condition`` FROM ``Component`` WHERE ``Component``='DesktopShortcut'")
    $desktopConditionView.Execute()
    $desktopCondition = $desktopConditionView.Fetch().StringData(1)
    if ($desktopCondition -notlike '*CREATE_DESKTOP_SHORTCUT*') {
        throw 'The desktop shortcut is not controlled by its installer checkbox.'
    }
    $licenseView = $msiDb.OpenView("SELECT ``Text`` FROM ``Control`` WHERE ``Dialog_``='LicenseAgreementDlg' AND ``Control``='LicenseText'")
    $licenseView.Execute()
    $embeddedLicense = $licenseView.Fetch()
    if (-not $embeddedLicense -or
        $embeddedLicense.StringData(1) -notlike '*GNU AFFERO GENERAL PUBLIC LICENSE*' -or
        $embeddedLicense.StringData(1) -notlike '*END OF TERMS AND CONDITIONS*') {
        throw 'The MSI license agreement page does not contain the repository LICENSE.'
    }
    $propertyView = $msiDb.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``='ProductCode'")
    $propertyView.Execute()
    if (-not $SkipInstallSmoke) {
        $smokeProductCode = $propertyView.Fetch().StringData(1)

        $smokeInstall = Join-Path $work 'installed'
        $installAttempted = $false
        $uninstalled = $false
        try {
            $installAttempted = $true
            $installRun = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', $installer, '/qn', '/norestart',
                'SKIP_SHORTCUTS=1', "INSTALLFOLDER=$smokeInstall") `
                -Wait -PassThru
            if ($installRun.ExitCode -ne 0) { throw "The MSI failed its silent install check (exit code $($installRun.ExitCode))." }
            $smokePython = Join-Path $smokeInstall 'python/python.exe'
            if (-not (Test-Path -LiteralPath $smokePython)) {
                throw 'The MSI did not put the app in the requested folder.'
            }
            & $smokePython (Join-Path $smokeInstall 'decomp-launch.pyw') --help | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'The installed app failed its relocated-runtime launch smoke check.' }
            $smokePythonw = Join-Path $smokeInstall 'python/pythonw.exe'
            $launchScript = Join-Path $smokeInstall 'decomp-launch.pyw'
            $launchRun = Start-Process -FilePath $smokePythonw -ArgumentList @("`"$launchScript`"", '--help') `
                -Wait -PassThru -WindowStyle Hidden
            if ($launchRun.ExitCode -ne 0) { throw 'The installed pythonw launcher failed its smoke check.' }
            $uninstallRun = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/x', $smokeProductCode, '/qn', '/norestart') `
                -Wait -PassThru
            if ($uninstallRun.ExitCode -ne 0) { throw 'The MSI failed its uninstall smoke check.' }
            $uninstalled = $true
        }
        finally {
            if ($installAttempted -and -not $uninstalled) {
                Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/x', $smokeProductCode, '/qn', '/norestart') `
                    -Wait -PassThru | Out-Null
            }
        }
    }
    Remove-Item -LiteralPath (Join-Path $dist "decompdecomp-$version-windows-x64.wixpdb") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $dist "decompdecomp-$version-windows-x64-setup.wixpdb") -Force -ErrorAction SilentlyContinue
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
