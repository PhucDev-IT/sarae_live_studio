param([string]$Apk='.tools/phase5-consumer/consumer-test-debug-487-lspatched.apk')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$root=Join-Path (Get-Location) 'test-results/phase5'
New-Item -ItemType Directory -Force $root | Out-Null
$total=[Diagnostics.Stopwatch]::StartNew()
function Adb([string[]]$Arguments) {
    if ($total.Elapsed.TotalSeconds -gt 280) { throw 'Phase 5 device budget exhausted' }
    $result=& adb.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "ADB failed: $Arguments" }
    return $result
}
function TikTokMetadata([string]$Name) {
    Adb @('shell','dumpsys','package','com.ss.android.ugc.trill') |
        Select-String 'codePath=|versionCode=|versionName=|lastUpdateTime=|firstInstallTime=|signatures=' |
        Set-Content "$root/$Name"
}
try {
    Adb @('get-state') | Set-Content "$root/device-state.txt"
    Adb @('shell','getprop','ro.product.model') | Add-Content "$root/device-state.txt"
    Adb @('shell','getprop','ro.build.version.sdk') | Add-Content "$root/device-state.txt"
    TikTokMetadata 'tiktok-before.txt'
    Adb @('shell','am','force-stop','co.sarae.camera.consumer') | Out-Null
    Adb @('install','-r',$Apk) | Set-Content "$root/install.txt"
    Adb @('push','test-fixtures/A.mp4','/sdcard/Android/data/co.sarae.camera.consumer/files/A.mp4') | Out-Null
    Adb @('push','test-fixtures/B-phase3.mp4','/sdcard/Android/data/co.sarae.camera.consumer/files/B.mp4') | Out-Null
    Adb @('shell','pm','grant','co.sarae.camera.consumer','android.permission.CAMERA') | Out-Null
    Adb @('shell','input','keyevent','KEYCODE_WAKEUP') | Out-Null
    Adb @('shell','wm','dismiss-keyguard') | Out-Null
    foreach ($method in @('modern','legacy','shared')) {
        $dir=Join-Path $root $method
        New-Item -ItemType Directory -Force $dir | Out-Null
        Adb @('shell','am','force-stop','co.sarae.camera.consumer') | Out-Null
        $capture=Start-Process -FilePath (Get-Command adb.exe).Source -WindowStyle Hidden -PassThru `
            -ArgumentList @('logcat','-v','threadtime','-T','1','SaraeBridge:I','SaraeConsumer:I','AndroidRuntime:E','libc:F','DEBUG:F','*:S') `
            -RedirectStandardOutput "$dir/relevant-logcat.txt" -RedirectStandardError "$dir/logcat-stderr.txt"
        $run=[Diagnostics.Stopwatch]::StartNew()
        $startUtc=[DateTime]::UtcNow.ToString('o')
        $testPid=$null
        try {
            Adb @('shell','am','start','-n','co.sarae.camera.consumer/.ConsumerActivity','--es','testSessionMethod',$method,'--ei','testWidth','1920','--ei','testHeight','1080') | Set-Content "$dir/start.txt"
            Start-Sleep -Seconds 2
            $testPid=(Adb @('shell','pidof','co.sarae.camera.consumer')).Trim()
            $count=if($method -eq 'modern'){4}elseif($method -eq 'legacy'){1}else{0}
            for ($i=1; $i -le $count; $i++) {
                if ($run.Elapsed.TotalSeconds -gt 50) { throw 'Run approached 60 second limit' }
                $source=if($i%2){'B'}else{'A'}
                $mode=if($method -eq 'modern' -and $i%2 -eq 0){'after'}else{'now'}
                @{index=$i;source=$source;mode=$mode;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress | Add-Content "$dir/switch-commands.jsonl"
                Adb @('shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p','co.sarae.camera.consumer','--es','source',$source,'--es','mode',$mode) | Out-Null
                Start-Sleep -Seconds $(if($mode -eq 'after'){5}else{2})
            }
            if($method -eq 'shared'){Start-Sleep -Seconds 2}
            $finalPid=(Adb @('shell','pidof','co.sarae.camera.consumer')).Trim()
            if($finalPid -ne $testPid){throw 'Consumer PID changed'}
        } finally {
            # Always leave the test consumer and allow queued release logs to flush.
            & adb.exe shell input keyevent KEYCODE_HOME | Out-Null
            Start-Sleep -Seconds 1
            $run.Stop()
            @{method=$method;pid=$testPid;startUtc=$startUtc;durationSeconds=$run.Elapsed.TotalSeconds} |
                ConvertTo-Json | Set-Content "$dir/run.json"
            if($testPid) {
                Adb @('pull',"/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/bridge-$testPid.jsonl","$dir/bridge.jsonl") | Out-Null
                Adb @('pull',"/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/consumer-$testPid.jsonl","$dir/consumer.jsonl") | Out-Null
                Adb @('logcat','-d',"--pid=$testPid",'-v','threadtime') | Set-Content "$dir/pid-logcat.txt"
            }
            Adb @('shell','dumpsys','activity','exit-info','co.sarae.camera.consumer') | Set-Content "$dir/exit-info.txt"
            if(!$capture.HasExited){Stop-Process -Id $capture.Id}
        }
        Write-Output "method=$method pid=$testPid durationSeconds=$($run.Elapsed.TotalSeconds)"
    }
    TikTokMetadata 'tiktok-after.txt'
} finally {
    $total.Stop()
    @{elapsedSeconds=$total.Elapsed.TotalSeconds;budgetSeconds=300;includes='install, file transfer, metadata, foreground runs, cleanup and diagnostic pulls'} |
        ConvertTo-Json | Set-Content "$root/device-budget.json"
}
