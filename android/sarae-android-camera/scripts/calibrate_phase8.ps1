param([ValidatePattern('^[a-z0-9-]+$')][string]$RunName='calibration')
$ErrorActionPreference='Stop'
throw 'Historical unbounded installer disabled after the connectivity overrun. Use bounded run_phase8.ps1; preserve the existing budget journals.'
Set-Location (Split-Path $PSScriptRoot -Parent)
$adb='D:/window/Android/Sdk/platform-tools/adb.exe'
$root="test-results/phase8/$RunName"
if(Test-Path $root){throw 'Preserve calibration evidence'}
New-Item -ItemType Directory $root | Out-Null
if((Get-Content -Raw test-results/phase8/update-static.json | ConvertFrom-Json).result -ne 'PASS'){throw 'Static gates required'}
$spent=(@(Get-Content test-results/phase8/device-budget.jsonl | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object seconds -Sum).Sum
$watch=[Diagnostics.Stopwatch]::StartNew()
function AdbChecked([string[]]$Arguments){
    if($spent+$watch.Elapsed.TotalSeconds -gt 120){throw 'Calibration budget reserve reached'}
    $out=& $adb @Arguments
    if($LASTEXITCODE -ne 0){throw "ADB failed: $Arguments"}
    return $out
}
function Ui([string]$Name){
    AdbChecked @('shell','uiautomator','dump','/sdcard/sarae-phase8-ui.xml') > "$root/$Name-dump.txt"
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
function SetRenderConfig([bool]$Auto,[string]$Label){
    @{autoCameraOrientation=$Auto;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080;diagnosticCaptureLabel=$Label} |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$root/config-$Label.json"
    AdbChecked @('push',"$root/config-$Label.json",'/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
}
try {
    $base=((Get-Content test-results/phase8/paths-before.txt | Where-Object {$_ -match '/base.apk$'}) -replace '^package:','').Trim()
    $hash=(AdbChecked @('shell','sha256sum',$base)) -join ''
    $hash > "$root/base-before-hash.txt"
    if(!$hash.StartsWith('f829a13fcbbe7168941f656c9b0e0e9c255f8d55e965b1a91a1a4af13e554bab')){throw 'Installed base differs from Phase 6/7; stop update'}
    $apk='.tools/phase8/patched/base-487-lspatched.apk'
    $bytes=(Get-Item $apk).Length
    AdbChecked @('push',$apk,'/data/local/tmp/sarae-phase8-base.apk') > "$root/push-base.txt"
    $created=(AdbChecked @('shell','pm','install-create','-r','-p','com.ss.android.ugc.trill','-S',"$bytes")) -join ''
    $created > "$root/install-create.txt"
    if($created -notmatch 'Success: created install session \[(\d+)\]'){throw 'No partial update session'}
    $installId=$Matches[1]
    try {
        AdbChecked @('shell','pm','install-write','-S',"$bytes",$installId,'base','/data/local/tmp/sarae-phase8-base.apk') > "$root/install-write.txt"
        AdbChecked @('shell','pm','install-commit',$installId) > "$root/install.txt"
        if((Get-Content -Raw "$root/install.txt").Trim() -ne 'Success'){throw 'Commit rejected; stop without bypass'}
    } catch {
        & $adb shell pm install-abandon $installId > "$root/install-abandon.txt"
        throw
    }
    SetRenderConfig $false 'before'
    AdbChecked @('shell','am','start','-W','-n','com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity') > "$root/launch.txt"
    $ui=Ui 'launch'
    if(-not ($ui.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){
        $quay=$ui.SelectNodes('//node') | Where-Object {$_.text -eq 'Quay' -or $_.'content-desc' -eq 'Quay'} | Select-Object -First 1
        TapNode $quay
        Start-Sleep -Milliseconds 700
        $mode=Ui 'mode'
        if(-not ($mode.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){
            TapNode ($mode.SelectNodes('//node') | Where-Object {$_.text -eq 'LIVE' -and $_.package -eq 'com.ss.android.ugc.trill'} | Select-Object -First 1)
        }
    }
    Start-Sleep -Seconds 2
    $ready=Ui 'prelive'
    if(-not ($ready.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){throw 'Not pre-broadcast preview; stop'}
    $testPid=(AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim()
    Shot 'preview-before'
    SetRenderConfig $true 'auto'
    AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_RENDER_CONFIG','-p','com.ss.android.ugc.trill') > "$root/reload.txt"
    Start-Sleep -Seconds 2
    Shot 'preview-auto'
    # End calibration captures. Subsequent cadence run has no readback or PNG I/O.
    @{autoCameraOrientation=$true;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080} |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM test-results/phase8/render-config.json
    AdbChecked @('push','test-results/phase8/render-config.json','/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
    AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_RENDER_CONFIG','-p','com.ss.android.ugc.trill') | Out-Null
    AdbChecked @('shell','dumpsys','package','com.ss.android.ugc.trill') | Select-String 'codePath=|versionCode=|versionName=|firstInstallTime=|lastUpdateTime=|signatures=|userId=' > "$root/package-after.txt"
    AdbChecked @('shell','pm','path','com.ss.android.ugc.trill') > "$root/paths-after.txt"
} finally {
    & $adb shell input keyevent KEYCODE_HOME | Out-Null
    Start-Sleep -Milliseconds 500
    & $adb pull /sdcard/Android/data/com.ss.android.ugc.trill/files/phase6 "$root/jsonl" | Out-Null
    & $adb pull /sdcard/Android/data/com.ss.android.ugc.trill/files/diagnostics "$root/diagnostics" | Out-Null
    & $adb logcat -b crash -d -v threadtime > "$root/crash-buffer.txt"
    & $adb logcat -d -v threadtime 'SaraeBridge:V' 'LSPatch:V' 'LSPosed:V' 'AndroidRuntime:V' 'libc:V' 'DEBUG:V' '*:S' > "$root/logcat.txt"
    $watch.Stop()
    @{stage='same_signer_partial_base_update_orientation_calibration';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress | Add-Content test-results/phase8/device-budget.jsonl
    @{pid=$testPid;switches=0;seconds=$watch.Elapsed.TotalSeconds;previewClosedWithHome=$true} | ConvertTo-Json > "$root/run.json"
}
