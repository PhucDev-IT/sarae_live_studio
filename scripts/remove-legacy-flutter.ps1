param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$root = git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) { throw 'Run inside the Sarae Git repository.' }
Set-Location $root
if ((git branch --show-current).Trim() -ne 'dev') { throw 'Switch to dev before cleanup.' }
$status = git status --porcelain
if ($status) { throw 'Git working tree is not clean. Commit/stash your changes first.' }
# Flutter workspace only. Intentionally preserve desktop/src, desktop/src-tauri, desktop/package.json, desktop/README.md.
$flutterDirs = @('lib','test','windows','linux','macos','ios','android','web','.dart_tool','.flutter-plugins','.flutter-plugins-dependencies')
$flutterFiles = @('pubspec.yaml','pubspec.lock','analysis_options.yaml','.metadata','.packages','flutter_launcher_icons.yaml')
$tracked = @(git ls-files -- desktop)
$obsolete = @($tracked | Where-Object {
    $path = $_ -replace '\\','/'
    $relative = $path.Substring('desktop/'.Length)
    $prefix = $relative.Split('/')[0]
    ($flutterDirs -contains $prefix) -or ($flutterFiles -contains $relative) -or
    ($relative -match '^[^/]+\\.iml$') -or ($relative -match '^\.idea/')
})
Write-Host ('Legacy Flutter tracked files: ' + $obsolete.Count)
$obsolete | ForEach-Object { Write-Host ('  ' + $_) }
if (!$Apply) { Write-Host 'Dry-run only. Pass -Apply to remove tracked Flutter files (git rm).'; exit 0 }
if (!$obsolete.Count) { Write-Host 'Nothing to remove.'; exit 0 }
$obsolete | ForEach-Object { git rm -- $_; if ($LASTEXITCODE -ne 0) { throw ('git rm failed for '+$_) } }
Write-Host 'Tracked Flutter workspace removed from Git. Review git diff --cached --stat, then commit/push.'
Write-Host 'This script never removes untracked data, local credentials or videos.'
