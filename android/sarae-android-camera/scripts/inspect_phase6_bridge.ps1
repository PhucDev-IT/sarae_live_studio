$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$root=Join-Path (Get-Location) 'test-results/phase6'
$dir=Join-Path $root 'bridge'
New-Item -ItemType Directory -Force $dir | Out-Null
$gate=Get-Content -Raw "$root/loader-only/run.json" | ConvertFrom-Json
$static=Get-Content -Raw "$root/bridge-static.json" | ConvertFrom-Json
if($gate.loaderGate -ne 'PASS' -or $static.result -ne 'PASS'){throw 'Loader and module artifact gates required'}
$spent=(@(Get-Content "$root/device-budget.jsonl" | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object -Property seconds -Sum).Sum
if($spent -gt 140){throw 'Insufficient budget reserved for preview and rollback'}
$watch=[Diagnostics.Stopwatch]::StartNew()
$capture=Start-Process -FilePath (Get-Command adb.exe).Source -WindowStyle Hidden -PassThru `
    -ArgumentList @('logcat','-b','all','-v','threadtime','-T','1','LSPatch:V','LSPosed:V','LSPlant:V','LSPosed-Bridge:V','SaraeBridge:V','AndroidRuntime:V','libc:V','DEBUG:V','ActivityManager:I','ActivityTaskManager:I','am_proc_start:I','am_proc_died:I','am_crash:I','am_anr:I','*:S') `
    -RedirectStandardOutput "$dir/startup-logcat.txt" -RedirectStandardError "$dir/logcat-stderr.txt"
try {
    $files=@($static.apks | ForEach-Object {Join-Path (Get-Location) ".tools/phase6/bridge/$($_.name)"})
    & adb.exe install-multiple -r @files *> "$dir/install.txt"
    if($LASTEXITCODE -ne 0){throw 'Module install rejected; stop without bypass'}
    & adb.exe shell mkdir -p /sdcard/Android/data/com.ss.android.ugc.trill/files
    if($LASTEXITCODE -ne 0){throw 'Could not provision lab fixture directory'}
    & adb.exe push test-fixtures/A.mp4 /sdcard/Android/data/com.ss.android.ugc.trill/files/A.mp4 | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Could not provision A'}
    & adb.exe push test-fixtures/B-phase3.mp4 /sdcard/Android/data/com.ss.android.ugc.trill/files/B.mp4 | Out-Null
    if($LASTEXITCODE -ne 0){throw 'Could not provision B'}
    & adb.exe shell pm grant com.ss.android.ugc.trill android.permission.CAMERA > "$dir/camera-permission.txt"
    if($LASTEXITCODE -ne 0){throw 'Camera permission grant failed'}
    $component=(Get-Content "$root/loader-only/launcher-before.txt" | Where-Object {$_ -match '^com\.ss\.android\.ugc\.trill/'} | Select-Object -Last 1).Trim()
    & adb.exe shell am start -W -n $component *> "$dir/launch.txt"
    Start-Sleep -Seconds 2
    $testPid=(& adb.exe shell pidof com.ss.android.ugc.trill) -join ' '
    & adb.exe shell uiautomator dump /sdcard/sarae-phase6-bridge.xml > "$dir/ui-dump-result.txt"
    & adb.exe pull /sdcard/sarae-phase6-bridge.xml "$dir/ui.xml" | Out-Null
    & adb.exe shell screencap -p /sdcard/sarae-phase6-bridge.png
    & adb.exe pull /sdcard/sarae-phase6-bridge.png "$dir/screen.png" | Out-Null
    & adb.exe shell dumpsys activity activities > "$dir/activities.txt"
    & adb.exe shell dumpsys activity exit-info com.ss.android.ugc.trill > "$dir/exit-info.txt"
    & adb.exe logcat -b crash -d -v threadtime > "$dir/crash-buffer.txt"
    if($testPid -match '^\d+$'){
        & adb.exe logcat -d "--pid=$testPid" -v threadtime > "$dir/pid-logcat.txt"
    }
    @{pid=$testPid;elapsedSeconds=$watch.Elapsed.TotalSeconds;switches=0} | ConvertTo-Json | Set-Content "$dir/run.json"
} finally {
    & adb.exe shell input keyevent KEYCODE_HOME | Out-Null
    if(!$capture.HasExited){Stop-Process -Id $capture.Id}
    $watch.Stop()
    @{stage='module_install_startup_ui_diagnostics';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Add-Content "$root/device-budget.jsonl"
}
