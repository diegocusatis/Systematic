param([Parameter(Mandatory=$true)][string]$OutputPath,
      [Parameter(Mandatory=$true)][string]$ReleasePath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$palette = (Get-Content -LiteralPath $ReleasePath -Raw | ConvertFrom-Json).colors
# PNG-backed ICO entries preserve the exact browser RGB values. Icon.FromHandle
# followed by Icon.Save can quantize the palette to 16 colors on Windows.
$sizes = @(16,32,48,64,256)
$images = [System.Collections.Generic.List[byte[]]]::new()
foreach ($size in $sizes) {
    $bitmap = [System.Drawing.Bitmap]::new($size,$size)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml($palette.green))
    $graphics.ScaleTransform($size / 64.0, $size / 64.0)
    $brush = [System.Drawing.SolidBrush]::new([System.Drawing.ColorTranslator]::FromHtml($palette.accent))
    $graphics.FillRectangle($brush,12,13,6,38)
    $font = [System.Drawing.Font]::new('Segoe UI',32,[System.Drawing.FontStyle]::Bold,[System.Drawing.GraphicsUnit]::Pixel)
    $graphics.DrawString('S',$font,[System.Drawing.Brushes]::White,20,10)
    $buffer = [System.IO.MemoryStream]::new()
    $bitmap.Save($buffer,[System.Drawing.Imaging.ImageFormat]::Png)
    $images.Add($buffer.ToArray())
    $buffer.Dispose()
    $font.Dispose()
    $brush.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}
$stream = [System.IO.File]::Create($OutputPath)
$writer = [System.IO.BinaryWriter]::new($stream)
try {
    $writer.Write([uint16]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $dimension = if ($sizes[$i] -eq 256) { 0 } else { $sizes[$i] }
        $writer.Write([byte]$dimension)
        $writer.Write([byte]$dimension)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$images[$i].Length)
        $writer.Write([uint32]$offset)
        $offset += $images[$i].Length
    }
    foreach ($bytes in $images) { $writer.Write([byte[]]$bytes) }
} finally {
    $writer.Dispose()
}
# Verify the stored icon, not just the drawing canvas.
function Assert-IconPalette {
    $reader = [System.IO.BinaryReader]::new([System.IO.File]::OpenRead($OutputPath))
    try {
        for ($i = 0; $i -lt $sizes.Count; $i++) {
            $null = $reader.BaseStream.Seek(6 + 16 * $i + 8,[System.IO.SeekOrigin]::Begin)
            $length = $reader.ReadUInt32()
            $position = $reader.ReadUInt32()
            $null = $reader.BaseStream.Seek($position,[System.IO.SeekOrigin]::Begin)
            $encoded = [System.IO.MemoryStream]::new($reader.ReadBytes($length))
            $check = [System.Drawing.Bitmap]::new($encoded)
            try {
                if ($check.GetPixel(0,0).ToArgb() -ne [System.Drawing.ColorTranslator]::FromHtml($palette.green).ToArgb()) {
                    throw "The saved icon did not preserve Systematic's browser green."
                }
            } finally {
                $check.Dispose()
                $encoded.Dispose()
            }
        }
    } finally {
        $reader.Dispose()
    }
}
Assert-IconPalette
