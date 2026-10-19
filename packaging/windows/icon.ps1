# Rebuild the Windows icon from the website favicon, without an image-tool dependency.
# This favicon uses only rectangles and straight horizontal/vertical path segments.
param([switch]$Development)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$source = Get-Content -LiteralPath (Join-Path $repoRoot 'site/public/favicon.svg') -Raw
# The Windows icon cannot follow the OS colour scheme; keep the favicon's two letter colours in either theme.
$source = $source -replace '\.i\{fill:#[0-9a-fA-F]+\}', '.i{fill:#edebe5}'
if ($Development) { $source = $source -replace '#f6d43a', '#3ac8f6' }
[xml]$svg = $source
$bounds = $svg.DocumentElement.GetAttribute('viewBox').Split(' ') | ForEach-Object { [float]::Parse($_, [cultureinfo]::InvariantCulture) }
$sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
$images = @()
foreach ($size in $sizes) {
    $bitmap = New-Object System.Drawing.Bitmap ($size * 4), ($size * 4)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = 'AntiAlias'
    $scale = $size * 4 / $bounds[2]
    $graphics.ScaleTransform($scale, $scale)
    $graphics.TranslateTransform(-$bounds[0], -$bounds[1])
    foreach ($node in $svg.DocumentElement.ChildNodes) {
        if ($node.LocalName -eq 'style') { continue }
        # Use the SVG's default palette; an ICO cannot switch colours with the OS theme.
        $class = [regex]::Escape($node.GetAttribute('class'))
        $fill = [regex]::Match([string]$svg.svg.style, "\.$class\{fill:(#[0-9a-fA-F]+)").Groups[1].Value
        if (-not $fill) { throw "No default fill for SVG class: $class" }
        $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($fill))
        if ($node.LocalName -eq 'rect') {
            $values = @('x', 'y', 'width', 'height') | ForEach-Object { [float]::Parse($node.GetAttribute($_), [cultureinfo]::InvariantCulture) }
            $graphics.FillRectangle($brush, $values[0], $values[1], $values[2], $values[3])
        } elseif ($node.LocalName -eq 'path') {
            $path = New-Object System.Drawing.Drawing2D.GraphicsPath
            $tokens = [regex]::Matches($node.GetAttribute('d'), '[a-zA-Z]|-?\d+(?:\.\d+)?')
            $x = 0; $y = 0
            for ($index = 0; $index -lt $tokens.Count; $index++) {
                $command = $tokens[$index].Value
                if ($command -eq 'z') { $path.CloseFigure(); continue }
                $index++
                $value = [float]::Parse($tokens[$index].Value, [cultureinfo]::InvariantCulture)
                $nextX = $x; $nextY = $y
                switch -CaseSensitive ($command) {
                    'M' { $x = $value; $index++; $y = [float]::Parse($tokens[$index].Value, [cultureinfo]::InvariantCulture); $path.StartFigure() }
                    'h' { $nextX += $value }
                    'H' { $nextX = $value }
                    'v' { $nextY += $value }
                    'V' { $nextY = $value }
                    default { throw "Unsupported SVG command: $command" }
                }
                if ($command -cne 'M') { $path.AddLine([float]$x, [float]$y, [float]$nextX, [float]$nextY); $x = $nextX; $y = $nextY }
            }
            $graphics.FillPath($brush, $path)
            $path.Dispose()
        } else { throw "Unsupported SVG element: $($node.LocalName)" }
        $brush.Dispose()
    }
    $small = New-Object System.Drawing.Bitmap $size, $size
    $resize = [System.Drawing.Graphics]::FromImage($small)
    $resize.InterpolationMode = 'HighQualityBicubic'
    $resize.DrawImage($bitmap, 0, 0, $size, $size)
    $stream = New-Object System.IO.MemoryStream
    $small.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $images += ,$stream.ToArray()
    $stream.Dispose(); $resize.Dispose(); $small.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}
$name = if ($Development) { 'favicon-dev.ico' } else { 'favicon.ico' }
$output = Join-Path $repoRoot "src/decompnet/app/static/$name"
$writer = New-Object System.IO.BinaryWriter ([System.IO.File]::Create($output))
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($index = 0; $index -lt $sizes.Count; $index++) {
        $dimension = $sizes[$index] % 256
        $writer.Write([byte]$dimension); $writer.Write([byte]$dimension)
        $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$index].Length); $writer.Write([uint32]$offset)
        $offset += $images[$index].Length
    }
    foreach ($png in $images) { $writer.Write([byte[]]$png) }
} finally { $writer.Dispose() }
