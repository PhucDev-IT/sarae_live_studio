param([ValidatePattern('^[a-z0-9-]+$')][string]$RunName='calibration-resume')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$root="test-results/phase8/$RunName"
if(Test-Path $root){throw 'Preserve evidence; do not overwrite'}
New-Item -ItemType Directory $root | Out-Null
$budget='test-results/phase8/device-budget-resume.jsonl'
$spent=if(Test-Path $budget){(@(Get-Content $budget | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object seconds -Sum).Sum}else{0}
if($spent -gt 200){throw 'Keep a full calibration and cleanup reserve'}
$watch=[Diagnostics.Stopwatch]::StartNew()
function AdbChecked([string[]]$Arguments,[int]$Timeout=12){
    $remaining=250-$spent-$watch.Elapsed.TotalSeconds
    if($remaining -lt 2){throw 'Budget reserve reached'}
    $limit=[Math]::Min($Timeout,$remaining)
    $out=& python scripts/adb_bounded.py --timeout $limit --journal "$root/adb-commands.jsonl" -- @Arguments
    if($LASTEXITCODE -ne 0){throw "ADB failed: $Arguments"}
    return $out
}
function Ui([string]$Name){
    AdbChecked @('shell','timeout','8','uiautomator','dump','/sdcard/sarae-phase8-ui.xml') > "$root/$Name-dump.txt"
    AdbChecked @('pull','/sdcard/sarae-phase8-ui.xml',"$root/$Name.xml") | Out-Null
    return [xml](Get-Content -Raw "$root/$Name.xml")
}
function TapNode($node){
    if(!$node -or $node.bounds -notmatch '^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$'){throw 'Missing safe navigation node'}
    $x=[int](([int]$Matches[1]+[int]$Matches[3])/2); $y=[int](([int]$Matches[2]+[int]$Matches[4])/2)
    AdbChecked @('shell','input','tap',"$x","$y") | Out-Null
}
function Shot([string]$name){
    AdbChecked @('shell','screencap','-p',"/sdcard/sarae-phase8-$name.png") | Out-Null
    AdbChecked @('pull',"/sdcard/sarae-phase8-$name.png","$root/$name.png") | Out-Null
}
try {
    AdbChecked @('get-state') > "$root/device.txt"
    @{autoCameraOrientation=$false;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080;diagnosticCaptureLabel='before'} |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$root/config-before.json"
    AdbChecked @('push',"$root/config-before.json",'/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
    AdbChecked @('shell','am','start','-W','-n','com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity') > "$root/launch.txt"
    # MainActivity already restores the existing camera preview. No taps or UI-idle waits.
    # Validate actual before/after screenshots offline before any switching run.
    Start-Sleep -Seconds 2
    $testPid=(AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim()
    Shot 'preview-before'
    @{autoCameraOrientation=$true;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080;diagnosticCaptureLabel='auto'} |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$root/config-auto.json"
    AdbChecked @('push',"$root/config-auto.json",'/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
    AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_RENDER_CONFIG','-p','com.ss.android.ugc.trill') > "$root/reload.txt"
    Start-Sleep -Seconds 2
    Shot 'preview-auto'
    AdbChecked @('shell','dumpsys','package','com.ss.android.ugc.trill') | Select-String 'codePath=|versionCode=|versionName=|firstInstallTime=|lastUpdateTime=|signatures=|userId=' > "$root/package-after.txt"
    AdbChecked @('shell','pm','path','com.ss.android.ugc.trill') > "$root/paths-after.txt"
} finally {
    # Always remove calibration-only readback from future sessions, including a UI failure.
    @{autoCameraOrientation=$true;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080} |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM test-results/phase8/render-config.json
    & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- push test-results/phase8/render-config.json /sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json | Out-Null
    & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- shell input keyevent KEYCODE_HOME | Out-Null
    Start-Sleep -Milliseconds 500
    & python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /sdcard/Android/data/com.ss.android.ugc.trill/files/phase6 "$root/jsonl" | Out-Null
    & python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /sdcard/Android/data/com.ss.android.ugc.trill/files/diagnostics "$root/diagnostics" | Out-Null
    & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- logcat -b crash -d -v threadtime > "$root/crash-buffer.txt"
    $watch.Stop()
    @{stage='resumed_orientation_calibration_no_reinstall';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress | Add-Content $budget
    @{pid=$testPid;switches=0;seconds=$watch.Elapsed.TotalSeconds;previewClosedWithHome=$true} | ConvertTo-Json > "$root/run.json"
}
