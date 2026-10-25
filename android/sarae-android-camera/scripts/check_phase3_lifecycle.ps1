$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
adb shell am force-stop co.sarae.camera.consumer
adb shell am start -n co.sarae.camera.consumer/.ConsumerActivity
Start-Sleep -Seconds 3
$run=@{pid=(adb shell pidof co.sarae.camera.consumer).Trim()}
New-Item -ItemType Directory -Force .tools/phase3-lifecycle,test-results/phase3/lifecycle | Out-Null
adb shell am start -n co.sarae.camera.consumer/.ConsumerActivity
Start-Sleep -Seconds 5
adb shell am broadcast -a co.sarae.camera.TEST_SWITCH -p co.sarae.camera.consumer --es source B --es mode now
Start-Sleep -Seconds 3
adb shell am broadcast -a co.sarae.camera.TEST_SWITCH -p co.sarae.camera.consumer --es source A --es mode after
Start-Sleep -Seconds 4
adb shell input keyevent KEYCODE_HOME
Start-Sleep -Seconds 3
adb shell am start -n co.sarae.camera.consumer/.ConsumerActivity
Start-Sleep -Seconds 5
adb shell am broadcast -a co.sarae.camera.TEST_SWITCH -p co.sarae.camera.consumer --es source B --es mode now
Start-Sleep -Seconds 2
adb shell input keyevent KEYCODE_HOME
Start-Sleep -Seconds 3
$observedPid=(adb shell pidof co.sarae.camera.consumer).Trim()
@{pidBefore=$run.pid;pidAfter=$observedPid;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content test-results/phase3/lifecycle/run.json
adb pull "/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/bridge-$($run.pid).jsonl" test-results/phase3/lifecycle/bridge.jsonl
adb pull "/sdcard/Android/data/co.sarae.camera.consumer/files/phase3/consumer-$($run.pid).jsonl" test-results/phase3/lifecycle/consumer.jsonl
adb logcat -d --pid=$observedPid -s AndroidRuntime:E libc:F DEBUG:F > test-results/phase3/lifecycle/pid-crash.txt
python scripts/analyze_phase3_lifecycle.py
adb shell am force-stop co.sarae.camera.consumer
