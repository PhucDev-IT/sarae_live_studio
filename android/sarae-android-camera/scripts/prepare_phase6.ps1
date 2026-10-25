$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$resultDir=Join-Path (Get-Location) 'test-results/phase6'
$originalDir=Join-Path (Get-Location) '.tools/phase6/original'
if(Test-Path (Join-Path $resultDir 'original-apks.json')){throw 'Original snapshot already exists. Preserve it; use a new execution directory for a new backup.'}
New-Item -ItemType Directory -Force $resultDir,$originalDir | Out-Null
$watch=[Diagnostics.Stopwatch]::StartNew()
function AdbChecked([string[]]$Arguments) {
    if($watch.Elapsed.TotalSeconds -gt 100){throw 'Preparation approached device budget allocation'}
    $output=& adb.exe @Arguments
    if($LASTEXITCODE -ne 0){throw "ADB failed: $Arguments"}
    return $output
}
try {
    AdbChecked @('get-state') | Set-Content "$resultDir/device.txt"
    AdbChecked @('shell','getprop','ro.product.model') | Add-Content "$resultDir/device.txt"
    AdbChecked @('shell','getprop','ro.build.version.sdk') | Add-Content "$resultDir/device.txt"
    AdbChecked @('shell','getprop','ro.product.cpu.abilist') | Add-Content "$resultDir/device.txt"
    AdbChecked @('shell','dumpsys','package','com.ss.android.ugc.trill') | Set-Content "$resultDir/package-before.txt"
    $paths=@(AdbChecked @('shell','pm','path','com.ss.android.ugc.trill'))
    $paths | Set-Content "$resultDir/paths-before.txt"
    $records=@()
    foreach($line in $paths){
        $remote=$line.Trim() -replace '^package:',''
        if($remote -notmatch '^/data/app/[A-Za-z0-9/+=._~-]+\.apk$'){throw "Unexpected APK path: $remote"}
        $name=[IO.Path]::GetFileName($remote)
        $remoteHash=(AdbChecked @('shell','sha256sum',$remote)) -join ''
        AdbChecked @('pull',$remote,(Join-Path $originalDir $name)) | Out-Null
        $localHash=(Get-FileHash -LiteralPath (Join-Path $originalDir $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if(-not $remoteHash.StartsWith($localHash)){throw "Hash mismatch: $name"}
        $records+=@{name=$name;remote=$remote;sha256=$localHash;bytes=(Get-Item -LiteralPath (Join-Path $originalDir $name)).Length}
    }
    $after=@(AdbChecked @('shell','pm','path','com.ss.android.ugc.trill'))
    $after | Set-Content "$resultDir/paths-after-pull.txt"
    if(($paths -join "`n") -ne ($after -join "`n")){throw 'Package paths changed during backup'}
    AdbChecked @('shell','dumpsys','package','com.ss.android.ugc.trill') | Set-Content "$resultDir/package-after-pull.txt"
    $records | ConvertTo-Json -Depth 5 | Set-Content "$resultDir/original-apks.json"
    Write-Output "Original APK files=$($records.Count) elapsedSeconds=$($watch.Elapsed.TotalSeconds)"
} finally {
    $watch.Stop()
    @{stage='original_snapshot';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Add-Content "$resultDir/device-budget.jsonl"
}
