param([switch]$AllowLabDataLoss)
$ErrorActionPreference='Stop'
if(-not $AllowLabDataLoss){throw 'Explicit -AllowLabDataLoss required: this replaces TikTok and deletes its current local account/application data.'}
Set-Location (Split-Path $PSScriptRoot -Parent)
$root=Join-Path (Get-Location) 'test-results/phase6'
$dir=Join-Path $root 'loader-only'
New-Item -ItemType Directory -Force $dir | Out-Null
$original=Get-Content -Raw "$root/original-static.json" | ConvertFrom-Json
$patched=Get-Content -Raw "$root/loader-only-static.json" | ConvertFrom-Json
if($original.result -ne 'PASS' -or $patched.result -ne 'PASS' -or $patched.apk_count -ne 61){throw 'Offline snapshot gates must pass first'}
$spent=(@(Get-Content "$root/device-budget.jsonl" | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object -Property seconds -Sum).Sum
if($spent -gt 100){throw 'Insufficient reserved device budget for install, startup and rollback'}
# Human authorization was explicitly provided in the current conversation before execution.
@{source='direct user reply';labDataLossAccepted=$true;reply='Đây là máy lab; chấp nhận thay TikTok và mất dữ liệu cục bộ'} |
    ConvertTo-Json | Set-Content "$root/lab-authorization.json"
$watch=[Diagnostics.Stopwatch]::StartNew()
$capture=$null
$installed=$false
try {
    & adb.exe shell cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER com.ss.android.ugc.trill > "$dir/launcher-before.txt"
    if($LASTEXITCODE -ne 0){throw 'Could not resolve actual installed launcher'}
    $component=(Get-Content "$dir/launcher-before.txt" | Where-Object {$_ -match '^com\.ss\.android\.ugc\.trill/[A-Za-z0-9.$]+$'} | Select-Object -Last 1).Trim()
    if(-not $component){throw 'No unambiguous launcher component'}
    & adb.exe logcat -b crash -d -v threadtime > "$dir/crash-before.txt"
    $capture=Start-Process -FilePath (Get-Command adb.exe).Source -WindowStyle Hidden -PassThru `
        -ArgumentList @('logcat','-b','all','-v','threadtime','-T','1','LSPatch:V','LSPosed:V','LSPlant:V','LSPosed-Bridge:V','AndroidRuntime:V','libc:V','DEBUG:V','ActivityManager:I','ActivityTaskManager:I','am_proc_start:I','am_proc_died:I','am_crash:I','am_anr:I','*:S') `
        -RedirectStandardOutput "$dir/startup-logcat.txt" -RedirectStandardError "$dir/logcat-stderr.txt"
    # Authorized lab replacement: no signature spoofing or in-place signature mismatch workaround.
    & adb.exe uninstall com.ss.android.ugc.trill *> "$dir/uninstall-original.txt"
    if($LASTEXITCODE -ne 0){throw 'Original lab uninstall failed'}
    $files=@($patched.apks | ForEach-Object {Join-Path (Get-Location) ".tools/phase6/loader-only/$($_.name)"})
    & adb.exe install-multiple @files *> "$dir/install.txt"
    if($LASTEXITCODE -ne 0){throw 'Loader package install rejected; stop without bypass'}
    $installed=$true
    & adb.exe shell dumpsys package com.ss.android.ugc.trill > "$dir/package-installed.txt"
    & adb.exe shell input keyevent KEYCODE_WAKEUP | Out-Null
    & adb.exe shell wm dismiss-keyguard | Out-Null
    & adb.exe shell am start -W -n $component *> "$dir/launch.txt"
    $launchCode=$LASTEXITCODE
    $observations=@()
    $fatal=$false
    for($i=0;$i -lt 6;$i++){
        Start-Sleep -Seconds 2
        $testPid=(& adb.exe shell pidof com.ss.android.ugc.trill) -join ' '
        $observations+=@{utc=[DateTime]::UtcNow.ToString('o');pid=$testPid}
        if((Get-Content "$dir/startup-logcat.txt" -Raw) -match 'Fatal signal|FATAL EXCEPTION|>>> com.ss.android.ugc.trill <<<'){$fatal=$true;break}
        if($watch.Elapsed.TotalSeconds+$spent -gt 200){break}
    }
    $observations | ConvertTo-Json | Set-Content "$dir/pid-observations.json"
    & adb.exe shell ps -A -o PID,PPID,NAME > "$dir/processes.txt"
    & adb.exe shell dumpsys activity activities > "$dir/activities.txt"
    & adb.exe shell dumpsys activity exit-info com.ss.android.ugc.trill > "$dir/exit-info.txt"
    & adb.exe logcat -b crash -d -v threadtime > "$dir/crash-after.txt"
    foreach($id in @($observations.pid | Where-Object {$_ -match '^\d+$'} | Select-Object -Unique)){
        & adb.exe logcat -d "--pid=$id" -v threadtime > "$dir/pid-$id-logcat.txt"
    }
    @{installed=$installed;launchExitCode=$launchCode;fatalObserved=$fatal;elapsedSeconds=$watch.Elapsed.TotalSeconds;
      loaderGate='PENDING_EVIDENCE_REVIEW';hookGate='NOT_RUN';injectionGate='NOT_RUN'} | ConvertTo-Json | Set-Content "$dir/run.json"
    & adb.exe shell input keyevent KEYCODE_HOME | Out-Null
    Write-Output "installed=$installed fatalObserved=$fatal elapsedSeconds=$($watch.Elapsed.TotalSeconds)"
} finally {
    if($capture -and -not $capture.HasExited){Stop-Process -Id $capture.Id}
    $watch.Stop()
    @{stage='loader_install_startup_diagnostics';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Add-Content "$root/device-budget.jsonl"
}
