param([int]$DurationSeconds = 1810, [int]$SwitchCount = 100)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$resultDir = Join-Path (Get-Location) 'test-results/phase3/endurance'
New-Item -ItemType Directory -Force $resultDir | Out-Null
function AdbChecked([string[]]$Arguments) {
    $result = & adb @Arguments
    if ($LASTEXITCODE -ne 0) { throw "ADB failed: $Arguments" }
    return $result
}
AdbChecked @('shell','am','force-stop','co.sarae.camera.consumer') | Out-Null
AdbChecked @('install','-r','.tools/phase3-consumer/consumer-test-debug-487-lspatched.apk') | Out-Null
AdbChecked @('push','test-fixtures/A.mp4','/sdcard/Android/data/co.sarae.camera.consumer/files/A.mp4') | Out-Null
AdbChecked @('push','test-fixtures/B-phase3.mp4','/sdcard/Android/data/co.sarae.camera.consumer/files/B.mp4') | Out-Null
AdbChecked @('shell','input','keyevent','KEYCODE_WAKEUP') | Out-Null
AdbChecked @('shell','wm','dismiss-keyguard') | Out-Null
AdbChecked @('shell','am','start','-n','co.sarae.camera.consumer/.ConsumerActivity') | Out-Null
Start-Sleep -Seconds 3
$testPid = (AdbChecked @('shell','pidof','co.sarae.camera.consumer')).Trim()
$started = [DateTime]::UtcNow
$watch = [Diagnostics.Stopwatch]::StartNew()
@{pid=$testPid; startUtc=$started.ToString('o'); requestedDurationSeconds=$DurationSeconds; requestedSwitches=$SwitchCount} |
    ConvertTo-Json | Set-Content "$resultDir/run.json"
$capture = Start-Process -FilePath (Get-Command adb).Source -ArgumentList @('logcat',"--pid=$testPid",'-v','threadtime') -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$resultDir/pid-logcat.txt" -RedirectStandardError "$resultDir/logcat-stderr.txt"
try {
    for ($index=1; $index -le $SwitchCount; $index++) {
        $source = if ($index % 2) {'B'} else {'A'}
        $mode = if ($index % 4 -in @(2,3)) {'after'} else {'now'}
        $observedPid = (AdbChecked @('shell','pidof','co.sarae.camera.consumer')).Trim()
        if ($observedPid -ne $testPid) { throw "PID changed: $testPid -> $observedPid" }
        @{index=$index;source=$source;mode=$mode;pid=$observedPid;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress |
            Add-Content "$resultDir/switch-commands.jsonl"
        AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p','co.sarae.camera.consumer','--es','source',$source,'--es','mode',$mode) | Out-Null
        Start-Sleep -Seconds $(if($mode -eq 'after'){5}else{2})
        if ($index % 10 -eq 0) { Write-Output "switches=$index elapsedSeconds=$([int]$watch.Elapsed.TotalSeconds) pid=$observedPid" }
    }
    $lastProgress=0
    while ($watch.Elapsed.TotalSeconds -lt $DurationSeconds) {
        Start-Sleep -Seconds 5
        $observedPid=(AdbChecked @('shell','pidof','co.sarae.camera.consumer')).Trim()
        if ($observedPid -ne $testPid) { throw "PID changed during endurance" }
        if ($watch.Elapsed.TotalSeconds-$lastProgress -ge 60) {
            $lastProgress=$watch.Elapsed.TotalSeconds
            $lastFrame=AdbChecked @('shell','tail','-n','1',"/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/consumer-$testPid.jsonl")
            Write-Output "elapsedSeconds=$([int]$lastProgress) pid=$observedPid last=$lastFrame"
        }
    }
    @{pid=$testPid;endUtc=[DateTime]::UtcNow.ToString('o');elapsedSeconds=$watch.Elapsed.TotalSeconds;switches=$SwitchCount} |
        ConvertTo-Json | Set-Content "$resultDir/completed.json"
} finally {
    AdbChecked @('shell','input','keyevent','KEYCODE_HOME') | Out-Null
    Start-Sleep -Seconds 2
    AdbChecked @('pull',"/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/bridge-$testPid.jsonl","$resultDir/bridge.jsonl") | Out-Null
    AdbChecked @('pull',"/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/consumer-$testPid.jsonl","$resultDir/consumer.jsonl") | Out-Null
    & adb logcat -d -b crash -v threadtime > "$resultDir/crash-buffer.txt"
    & adb logcat -d -s AndroidRuntime:E libc:F DEBUG:F -v threadtime > "$resultDir/native-java-diagnostics.txt"
    & adb shell dumpsys activity exit-info co.sarae.camera.consumer > "$resultDir/exit-info.txt"
    if (!$capture.HasExited) { Stop-Process -Id $capture.Id }
}
