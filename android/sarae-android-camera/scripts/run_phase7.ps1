param([ValidatePattern('^[a-z0-9-]+$')][string]$RunName='trace-run',
      [int]$TraceSeconds=30,[int]$QuietBeforeSeconds=8,[int]$SwitchWaitSeconds=2,
      [ValidateSet('A','B')][string]$FirstNextSource='B')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$budgetRoot=Join-Path (Get-Location) 'test-results/phase7'
$root=Join-Path $budgetRoot $RunName
if(Test-Path $root){throw 'Evidence directory exists; choose a fresh run name'}
New-Item -ItemType Directory $root | Out-Null
$spent=(@(Get-Content "$budgetRoot/device-budget.jsonl" | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object -Property seconds -Sum).Sum
if($spent -gt 180){throw 'Reserve device budget; do not start this run'}
$priorSwitches=0
foreach($journal in (Get-ChildItem -LiteralPath $budgetRoot -Recurse -Filter switch-commands.jsonl)){
    $priorSwitches+=@(Get-Content -LiteralPath $journal.FullName).Count
}
if($priorSwitches+5 -gt 10){throw 'Phase 7 already used the allowed switch count; do not repeat tests'}
$watch=[Diagnostics.Stopwatch]::StartNew()
$capture=$null
$traceStarted=$false
function AdbChecked([string[]]$Arguments){
    if($watch.Elapsed.TotalSeconds+$spent -gt 280){throw 'Device budget reserve reached'}
    $output=& adb.exe @Arguments
    if($LASTEXITCODE -ne 0){throw "ADB failed: $Arguments"}
    return $output
}
function Mark([string]$Stage){
    @{stage=$Stage;utc=[DateTime]::UtcNow.ToString('o');runElapsedSeconds=$watch.Elapsed.TotalSeconds} |
        ConvertTo-Json -Compress | Add-Content "$root/protocol.jsonl"
}
function Shot([string]$Name){
    Mark "screenshot_start_$Name"
    AdbChecked @('shell','screencap','-p',"/sdcard/sarae-phase7-$Name.png") | Out-Null
    AdbChecked @('pull',"/sdcard/sarae-phase7-$Name.png","$root/$Name.png") | Out-Null
    Mark "screenshot_end_$Name"
}
try {
    AdbChecked @('shell','am','start','-W','-n','com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity') | Set-Content "$root/launch-run.txt"
    AdbChecked @('shell','input','tap','540','2196') | Out-Null
    Start-Sleep -Milliseconds 700
    # Read the current mode: TikTok can restore a different tab position after HOME.
    AdbChecked @('shell','uiautomator','dump','/sdcard/sarae-phase7-mode.xml') | Set-Content "$root/mode-dump.txt"
    AdbChecked @('pull','/sdcard/sarae-phase7-mode.xml',"$root/ui-mode.xml") | Out-Null
    [xml]$modeUi=Get-Content -Raw "$root/ui-mode.xml"
    if(-not ($modeUi.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){
        $tab=$modeUi.SelectNodes('//node') | Where-Object {$_.text -eq 'LIVE' -and $_.package -eq 'com.ss.android.ugc.trill'} | Select-Object -First 1
        if(-not $tab -or $tab.bounds -notmatch '^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$'){throw 'Cannot identify LIVE mode tab safely'}
        $tapX=[int](($Matches[1] -as [int])+($Matches[3] -as [int]))/2
        $tapY=[int](($Matches[2] -as [int])+($Matches[4] -as [int]))/2
        AdbChecked @('shell','input','tap',"$([int]$tapX)","$([int]$tapY)") | Out-Null
    }
    Start-Sleep -Seconds 2
    AdbChecked @('shell','uiautomator','dump','/sdcard/sarae-phase7-prelive.xml') | Set-Content "$root/prelive-dump.txt"
    AdbChecked @('pull','/sdcard/sarae-phase7-prelive.xml',"$root/ui-prelive.xml") | Out-Null
    [xml]$ui=Get-Content -Raw "$root/ui-prelive.xml"
    if(-not ($ui.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){throw 'Expected pre-broadcast screen not present; stop without further UI actions'}
    $testPid=(AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim()
    $testPid | Set-Content "$root/pid-run.txt"
    AdbChecked @('shell','ps','-T','-p',$testPid) | Set-Content "$root/threads-before.txt"
    $capture=Start-Process -FilePath (Get-Command adb.exe).Source -WindowStyle Hidden -PassThru `
        -ArgumentList @('logcat','-b','all','-v','threadtime','-T','1','SaraeBridge:V','LSPatch:V','LSPosed:V','LSPlant:V','AndroidRuntime:V','libc:V','DEBUG:V','*:S') `
        -RedirectStandardOutput "$root/relevant-logcat.txt" -RedirectStandardError "$root/logcat-stderr.txt"
    $initialSource=if($FirstNextSource -eq 'B'){'A'}else{'B'}
    Shot "$initialSource-before-trace"
    AdbChecked @('shell','cat','/proc/uptime') | Set-Content "$root/uptime-before-trace.txt"
    # On this Samsung build perfetto cannot open shell-created /data/local/tmp configs.
    # Use its documented stdin configuration, without changing SELinux or permissions.
    Get-Content -Raw scripts/phase7-trace.pbtxt | & adb.exe shell perfetto --background-wait --txt -c - -o /data/misc/perfetto-traces/sarae-phase7.pftrace > "$root/perfetto-start.txt"
    if($LASTEXITCODE -ne 0){throw 'Perfetto start failed'}
    $traceStarted=$true
    $traceWatch=[Diagnostics.Stopwatch]::StartNew()
    Mark 'quiet_normal_start'
    Start-Sleep -Seconds $QuietBeforeSeconds
    Mark 'switching_start'
    for($i=1;$i -le 5;$i++){
        $source=if($i%2){$FirstNextSource}else{$initialSource}
        @{index=$i;source=$source;mode='now';utc=[DateTime]::UtcNow.ToString('o');runElapsedSeconds=$watch.Elapsed.TotalSeconds;pid=$testPid} |
            ConvertTo-Json -Compress | Add-Content "$root/switch-commands.jsonl"
        AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p','com.ss.android.ugc.trill','--es','source',$source,'--es','mode','now') | Out-Null
        Start-Sleep -Seconds $SwitchWaitSeconds
        if((AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim() -ne $testPid){throw 'PID changed during switching'}
    }
    Mark 'quiet_after_switch_start'
    while($traceWatch.Elapsed.TotalSeconds -lt ($TraceSeconds+1)){Start-Sleep -Seconds 1}
    Mark 'trace_capture_finished'
    Shot "$FirstNextSource-after-trace-1"
    Start-Sleep -Milliseconds 700
    Shot "$FirstNextSource-after-trace-2"
    AdbChecked @('shell','uiautomator','dump','/sdcard/sarae-phase7-final.xml') | Set-Content "$root/final-ui-dump.txt"
    AdbChecked @('pull','/sdcard/sarae-phase7-final.xml',"$root/ui-after.xml") | Out-Null
    Mark 'cleanup_home'
} finally {
    & adb.exe shell input keyevent KEYCODE_HOME | Out-Null
    Start-Sleep -Seconds 1
    if($capture -and -not $capture.HasExited){Stop-Process -Id $capture.Id}
    if($traceStarted){& adb.exe pull /data/misc/perfetto-traces/sarae-phase7.pftrace "$root/pipeline.pftrace" | Out-Null}
    & adb.exe pull /sdcard/Android/data/com.ss.android.ugc.trill/files/phase6 "$root/jsonl" | Out-Null
    & adb.exe logcat -b crash -d -v threadtime > "$root/crash-buffer.txt"
    & adb.exe shell dumpsys activity exit-info com.ss.android.ugc.trill > "$root/exit-info.txt"
    & adb.exe shell dumpsys package com.ss.android.ugc.trill | Select-String 'codePath=|versionCode=|versionName=|lastUpdateTime=|signatures=' | Set-Content "$root/package-after.txt"
    $watch.Stop()
    @{stage='real_mp4_trace_five_switches_cleanup';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json -Compress | Add-Content "$budgetRoot/device-budget.jsonl"
    $executedSwitches=if(Test-Path "$root/switch-commands.jsonl"){@(Get-Content "$root/switch-commands.jsonl").Count}else{0}
    @{pid=$testPid;switches=$executedSwitches;seconds=$watch.Elapsed.TotalSeconds;apkChanged=$false;traceStarted=$traceStarted;previewClosedWithHome=$true} |
        ConvertTo-Json | Set-Content "$root/run.json"
}
