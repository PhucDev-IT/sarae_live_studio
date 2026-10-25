param([switch]$RestoreLabSnapshot)
$ErrorActionPreference='Stop'
if(-not $RestoreLabSnapshot){throw 'Specify -RestoreLabSnapshot only for the authorized Phase 6 lab installation; local test app data will be lost.'}
Set-Location (Split-Path $PSScriptRoot -Parent)
$root=Join-Path (Get-Location) 'test-results/phase6'
$watch=[Diagnostics.Stopwatch]::StartNew()
try {
    $records=Get-Content -Raw "$root/original-apks.json" | ConvertFrom-Json
    $files=@()
    foreach($record in $records){
        $path=Join-Path (Get-Location) ".tools/phase6/original/$($record.name)"
        if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256){throw 'Rollback APK hash mismatch'}
        $files+=$path
    }
    if($files.Count -ne 61){throw 'Incomplete rollback set'}
    & adb.exe uninstall com.ss.android.ugc.trill *> "$root/rollback-uninstall.txt"
    if($LASTEXITCODE -ne 0){throw 'Could not remove lab test installation; stop before rollback install'}
    & adb.exe install-multiple @files *> "$root/rollback-install.txt"
    if($LASTEXITCODE -ne 0){throw 'Original snapshot reinstall failed; retain APKs and inspect log'}
    & adb.exe shell dumpsys package com.ss.android.ugc.trill > "$root/package-restored.txt"
    if($LASTEXITCODE -ne 0){throw 'Rollback verification failed'}
    Write-Output 'Original 61-APK snapshot restored. Prior application data/accounts are not restored.'
} finally {
    $watch.Stop()
    @{stage='rollback';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Add-Content "$root/device-budget.jsonl"
}
