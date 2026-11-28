<#
.SYNOPSIS
  Tai FFmpeg/FFprobe (ban Windows "essentials" cua gyan.dev, nguon Windows duoc ffmpeg.org gioi thieu)
  mot lan, kiem SHA256 da ghim, roi dat vao desktop\src-tauri\resources\ffmpeg\ de Tauri dong goi.

.DESCRIPTION
  - Phien ban va SHA256 duoc ghim ben duoi. Hai nguon doc lap (gyan.dev va GitHub release
    GyanD/codexffmpeg) cong bo cung mot SHA256 cho file zip nay.
  - File zip duoc cache trong desktop\.cache\ffmpeg\ (khong commit). Lan sau chi kiem lai checksum.
  - Chi giai nen dung 3 file: bin\ffmpeg.exe, bin\ffprobe.exe, LICENSE (GPLv3, bat buoc gui kem).
  - -Zip <duong dan>: dung file zip da tai san (may build khong co mang); van kiem SHA256.

  Doi phien ban FFmpeg: sua $Version, $Sha256, $Size; lay SHA256 tu
  https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-<ver>-essentials_build.zip.sha256 va doi chieu
  voi truong "digest" cua asset tren https://github.com/GyanD/codexffmpeg/releases/tag/<ver>.
#>
[CmdletBinding()]
param(
  [string]$Zip = "",
  [switch]$Force
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Version = "9.0.2"
$Sha256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"
$Size = 114768076
$Name = "ffmpeg-$Version-essentials_build.zip"
$Urls = @(
  "https://www.gyan.dev/ffmpeg/builds/packages/$Name",
  "https://github.com/GyanD/codexffmpeg/releases/download/$Version/$Name"
)

$Desktop = Split-Path -Parent $PSScriptRoot
$CacheDir = Join-Path $Desktop ".cache\ffmpeg"
$OutDir = Join-Path $Desktop "src-tauri\resources\ffmpeg"
$Stamp = Join-Path $OutDir "VERSION.txt"
$Wanted = @{ "bin/ffmpeg.exe" = "ffmpeg.exe"; "bin/ffprobe.exe" = "ffprobe.exe"; "LICENSE" = "LICENSE.txt" }

function Get-Sha256([string]$Path) {
  (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Test-Installed {
  if ($Force -or -not (Test-Path -LiteralPath $Stamp)) { return $false }
  $lines = Get-Content -LiteralPath $Stamp -Encoding UTF8
  if (-not ($lines -contains "zip_sha256=$Sha256")) { return $false }
  foreach ($file in $Wanted.Values) {
    $path = Join-Path $OutDir $file
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    if (-not ($lines -contains "$file=$(Get-Sha256 $path)")) { return $false }
  }
  return $true
}

if (Test-Installed) {
  Write-Host "[ffmpeg] Da co FFmpeg $Version (checksum khop): $OutDir"
  exit 0
}

if ($Zip) {
  $Archive = (Resolve-Path -LiteralPath $Zip).Path
} else {
  New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
  $Archive = Join-Path $CacheDir $Name
  if ((Test-Path -LiteralPath $Archive) -and ((Get-Sha256 $Archive) -ne $Sha256)) {
    Write-Host "[ffmpeg] File cache sai checksum, tai lai"
    Remove-Item -LiteralPath $Archive -Force
  }
  if (-not (Test-Path -LiteralPath $Archive)) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $partial = "$Archive.partial"
    $done = $false
    foreach ($url in $Urls) {
      try {
        Write-Host "[ffmpeg] Tai $url (~$([math]::Round($Size / 1MB)) MB)"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $partial
        $done = $true
        break
      } catch {
        Write-Host "[ffmpeg] Khong tai duoc tu nguon nay: $($_.Exception.Message)"
      }
    }
    if (-not $done) { throw "Khong tai duoc FFmpeg tu nguon nao." }
    Move-Item -LiteralPath $partial -Destination $Archive -Force
  }
}

$actualSize = (Get-Item -LiteralPath $Archive).Length
$actual = Get-Sha256 $Archive
if ($actual -ne $Sha256 -or $actualSize -ne $Size) {
  throw "Checksum FFmpeg KHONG khop (sha256 $actual, $actualSize byte). Khong dung file nay: $Archive"
}
Write-Host "[ffmpeg] SHA256 khop: $Sha256"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$staging = Join-Path $Desktop ".cache\ffmpeg\staging"
if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null
$prefix = "ffmpeg-$Version-essentials_build/"
$found = @{}
$archiveHandle = [System.IO.Compression.ZipFile]::OpenRead($Archive)
try {
  foreach ($entry in $archiveHandle.Entries) {
    $relative = $entry.FullName.Replace("\", "/")
    if (-not $relative.StartsWith($prefix)) { continue }
    $relative = $relative.Substring($prefix.Length)
    if ($Wanted.ContainsKey($relative)) {
      # Fixed output names: entry paths from the archive are never used as file paths.
      $target = Join-Path $staging $Wanted[$relative]
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
      $found[$relative] = $true
    }
  }
} finally {
  $archiveHandle.Dispose()
}
foreach ($key in $Wanted.Keys) {
  if (-not $found.ContainsKey($key)) { throw "Zip FFmpeg thieu $key" }
}

$versionLine = (& (Join-Path $staging "ffmpeg.exe") -hide_banner -version | Select-Object -First 1)
if ($versionLine -notmatch [regex]::Escape($Version)) { throw "ffmpeg.exe bao phien ban la: $versionLine" }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$stampLines = @("version=$Version", "source=$($Urls[0])", "zip_sha256=$Sha256")
foreach ($file in $Wanted.Values) {
  Copy-Item -LiteralPath (Join-Path $staging $file) -Destination (Join-Path $OutDir $file) -Force
  $stampLines += "$file=$(Get-Sha256 (Join-Path $OutDir $file))"
}
Set-Content -LiteralPath $Stamp -Value $stampLines -Encoding UTF8
Remove-Item -LiteralPath $staging -Recurse -Force
Write-Host "[ffmpeg] OK: $OutDir ($versionLine)"
