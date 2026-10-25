$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$root='test-results/phase8/live-calibration'
if(Test-Path $root){throw 'Preserve evidence'}
New-Item -ItemType Directory $root | Out-Null
$spent=(@(Get-Content test-results/phase8/device-budget-resume.jsonl | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object seconds -Sum).Sum
if($spent -gt 210){throw 'Insufficient resumed budget'}
$watch=[Diagnostics.Stopwatch]::StartNew()
function AdbChecked([string[]]$Arguments,[int]$Timeout=12){
 if($watch.Elapsed.TotalSeconds+$spent -gt 260){throw 'Reserve reached'}
 $out=& python scripts/adb_bounded.py --timeout $Timeout --journal "$root/adb-commands.jsonl" -- @Arguments
 if($LASTEXITCODE -ne 0){throw "ADB: $Arguments"}; return $out
}
function Config([bool]$Auto,$Override,[string]$Label){
 @{autoCameraOrientation=$Auto;rotationOverride=$Override;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080;diagnosticCaptureLabel=$Label} | ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$root/config-$Label.json"
 AdbChecked @('push',"$root/config-$Label.json",'/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
 AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_RENDER_CONFIG','-p','com.ss.android.ugc.trill') | Out-Null
}
function Shot([string]$Name){
 AdbChecked @('shell','screencap','-p',"/sdcard/sarae-phase8-$Name.png") | Out-Null
 AdbChecked @('pull',"/sdcard/sarae-phase8-$Name.png","$root/$Name.png") | Out-Null
}
try {
 Config $false $null 'live-before'
 AdbChecked @('shell','am','start','-W','-n','com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity') > "$root/launch.txt"
 Start-Sleep -Seconds 2
 AdbChecked @('shell','input','tap','540','2196') | Out-Null
 Start-Sleep -Seconds 2
 # LIVE mode tab bounds verified in navigation/ui.xml; this is not the broadcast button.
 AdbChecked @('shell','input','tap','911','2175') | Out-Null
 Start-Sleep -Seconds 3
 Shot 'live-before'
 AdbChecked @('shell','timeout','15','uiautomator','dump','--compressed','/sdcard/sarae-phase8-live.xml') 18 > "$root/ui-dump.txt"
 AdbChecked @('pull','/sdcard/sarae-phase8-live.xml',"$root/ui-before.xml") | Out-Null
 [xml]$ui=Get-Content -Raw "$root/ui-before.xml"
 if(-not ($ui.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){throw 'Pre-broadcast LIVE mode not confirmed'}
 Config $true $null 'live-auto-initial'
 Start-Sleep -Seconds 2
 Shot 'live-auto-initial'
 Config $true 270 'live-calibration-270'
 Start-Sleep -Seconds 2
 Shot 'live-calibration-270'
} finally {
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- shell input keyevent KEYCODE_HOME | Out-Null
 Start-Sleep -Milliseconds 500
 & python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /sdcard/Android/data/com.ss.android.ugc.trill/files/phase6 "$root/jsonl" | Out-Null
 & python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /sdcard/Android/data/com.ss.android.ugc.trill/files/diagnostics "$root/diagnostics" | Out-Null
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- logcat -b crash -d -v threadtime > "$root/crash-buffer.txt"
 $watch.Stop(); @{stage='live_orientation_mapping_calibration';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress | Add-Content test-results/phase8/device-budget-resume.jsonl
}
