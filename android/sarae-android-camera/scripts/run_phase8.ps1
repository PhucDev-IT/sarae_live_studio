param([ValidatePattern('^[a-z0-9-]+$')][string]$RunName='auto-run',[switch]$UpdateModule,
      [ValidatePattern('^[a-z0-9-]+$')][string]$ApkStage='auto-corrected')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$budget='test-results/phase8/device-budget-resume.jsonl'
$spent=(@(Get-Content $budget | ForEach-Object {ConvertFrom-Json $_}) | Measure-Object seconds -Sum).Sum
if($spent -gt 150){throw 'Insufficient 150-second reserve for this bounded run'}
$root="test-results/phase8/$RunName"
if(Test-Path $root){throw 'Preserve run evidence'}
$prior=0
Get-ChildItem test-results/phase8 -Recurse -Filter switch-commands.jsonl | ForEach-Object {$prior+=@(Get-Content $_.FullName).Count}
if($prior+5 -gt 10){throw 'Switch limit reached'}
New-Item -ItemType Directory $root | Out-Null
$watch=[Diagnostics.Stopwatch]::StartNew()
$traceStarted=$false
function AdbChecked([string[]]$Arguments,[int]$Timeout=12,[string]$InputFile=''){
 $remaining=255-$spent-$watch.Elapsed.TotalSeconds
 if($remaining -lt 2){throw 'Reserved cleanup budget reached'}
 $flags=@('--timeout',"$([Math]::Min($Timeout,$remaining))",'--journal',"$root/adb-commands.jsonl")
 if($InputFile){$flags+=@('--stdin-file',$InputFile)}
 $out=& python scripts/adb_bounded.py @flags -- @Arguments
 if($LASTEXITCODE -ne 0){throw "ADB: $Arguments"}; return $out
}
function Ui([string]$Name){
 AdbChecked @('shell','timeout','15','uiautomator','dump','--compressed','/sdcard/sarae-phase8-ui.xml') 18 > "$root/$Name-dump.txt"
 AdbChecked @('pull','/sdcard/sarae-phase8-ui.xml',"$root/$Name.xml") | Out-Null
 return [xml](Get-Content -Raw "$root/$Name.xml")
}
function Shot([string]$Name){
 AdbChecked @('shell','screencap','-p',"/sdcard/sarae-phase8-$Name.png") | Out-Null
 AdbChecked @('pull',"/sdcard/sarae-phase8-$Name.png","$root/$Name.png") | Out-Null
}
try {
 if($UpdateModule){
    $gate=Get-Content -Raw "test-results/phase8/$ApkStage-update-static.json" | ConvertFrom-Json
    if($gate.result -ne 'PASS'){throw 'Static gate required'}
    $apk=".tools/phase8/$ApkStage/base-487-lspatched.apk"
    if((Get-FileHash $apk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $gate.base_sha256){throw 'Artifact changed'}
    $bytes=(Get-Item $apk).Length
    AdbChecked @('push',$apk,'/data/local/tmp/sarae-phase8-base.apk') 20 > "$root/push-base.txt"
    $created=(AdbChecked @('shell','pm','install-create','-r','-p','com.ss.android.ugc.trill','-S',"$bytes")) -join ''
    $created > "$root/install-create.txt"
    if($created -notmatch 'Success: created install session \[(\d+)\]'){throw 'No update session'}
    $installId=$Matches[1]
    try {
        AdbChecked @('shell','pm','install-write','-S',"$bytes",$installId,'base','/data/local/tmp/sarae-phase8-base.apk') 20 > "$root/install-write.txt"
        AdbChecked @('shell','pm','install-commit',$installId) 20 > "$root/install.txt"
        if((Get-Content -Raw "$root/install.txt").Trim() -ne 'Success'){throw 'Update rejected'}
    } catch {
        & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- shell pm install-abandon $installId > "$root/install-abandon.txt"
        throw
    }
 }
 @{autoCameraOrientation=$true;rotationOverride=$null;scaleMode='FIT';mirrorX=$false;expectedWidth=1920;expectedHeight=1080} | ConvertTo-Json | Set-Content -Encoding utf8NoBOM "$root/render-config.json"
 AdbChecked @('push',"$root/render-config.json",'/sdcard/Android/data/com.ss.android.ugc.trill/files/sarae-render.json') | Out-Null
 AdbChecked @('shell','am','start','-W','-n','com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity') > "$root/launch.txt"
 Start-Sleep -Seconds 3
 AdbChecked @('shell','input','tap','540','2196') | Out-Null
 Start-Sleep -Seconds 3
 $mode=Ui 'mode'
 if(-not ($mode.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){
    $tab=$mode.SelectNodes('//node') | Where-Object {$_.text -eq 'LIVE' -and $_.package -eq 'com.ss.android.ugc.trill'} | Select-Object -First 1
    if(!$tab -or $tab.bounds -notmatch '^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$'){throw 'LIVE tab not found'}
    $x=[int](([int]$Matches[1]+[int]$Matches[3])/2);$y=[int](([int]$Matches[2]+[int]$Matches[4])/2)
    AdbChecked @('shell','input','tap',"$x","$y") | Out-Null
    Start-Sleep -Seconds 2
 }
 $ui=Ui 'prelive'
 if(-not ($ui.SelectNodes('//node') | Where-Object {$_.text -eq 'Phát LIVE'})){throw 'Not pre-broadcast preview'}
 $testPid=(AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim()
 Shot 'A-before'
 (Get-Content -Raw scripts/phase7-trace.pbtxt).Replace('duration_ms: 30000','duration_ms: 35000') | Set-Content -Encoding utf8NoBOM "$root/trace-config.pbtxt"
 AdbChecked @('shell','perfetto','--background-wait','--txt','-c','-','-o','/data/misc/perfetto-traces/sarae-phase8.pftrace') 12 "$root/trace-config.pbtxt" > "$root/perfetto-start.txt"
 $traceStarted=$true
 $traceWatch=[Diagnostics.Stopwatch]::StartNew()
 Start-Sleep -Seconds 8
 for($i=1;$i -le 5;$i++){
    $source=if($i%2){'B'}else{'A'}
    @{index=$i;source=$source;mode='now';utc=[DateTime]::UtcNow.ToString('o');elapsedSeconds=$watch.Elapsed.TotalSeconds} | ConvertTo-Json -Compress | Add-Content "$root/switch-commands.jsonl"
    AdbChecked @('shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p','com.ss.android.ugc.trill','--es','source',$source,'--es','mode','now') > "$root/switch-$i.txt"
    Start-Sleep -Seconds 2
    Shot "switch-$i-$source"
    if((AdbChecked @('shell','pidof','com.ss.android.ugc.trill')).Trim() -ne $testPid){throw 'PID changed'}
 }
 while($traceWatch.Elapsed.TotalSeconds -lt 36){Start-Sleep -Milliseconds 500}
 Shot 'B-after'
 AdbChecked @('shell','dumpsys','package','com.ss.android.ugc.trill') | Select-String 'codePath=|versionCode=|versionName=|firstInstallTime=|lastUpdateTime=|signatures=|userId=' > "$root/package-after.txt"
 AdbChecked @('shell','pm','path','com.ss.android.ugc.trill') > "$root/paths-after.txt"
} finally {
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- shell input keyevent KEYCODE_HOME | Out-Null
 Start-Sleep -Milliseconds 500
 if($traceStarted){& python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /data/misc/perfetto-traces/sarae-phase8.pftrace "$root/pipeline.pftrace" | Out-Null}
 & python scripts/adb_bounded.py --timeout 8 --journal "$root/adb-commands.jsonl" -- pull /sdcard/Android/data/com.ss.android.ugc.trill/files/phase6 "$root/jsonl" | Out-Null
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- logcat -b crash -d -v threadtime > "$root/crash-buffer.txt"
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- logcat -d -v threadtime SaraeBridge:V LSPatch:V LSPosed:V AndroidRuntime:V libc:V DEBUG:V '*:S' > "$root/logcat.txt"
 & python scripts/adb_bounded.py --timeout 5 --journal "$root/adb-commands.jsonl" -- shell dumpsys activity exit-info com.ss.android.ugc.trill > "$root/exit-info.txt"
 $watch.Stop()
 @{stage='auto_orientation_five_switches_trace';seconds=$watch.Elapsed.TotalSeconds;utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Compress | Add-Content $budget
 $count=if(Test-Path "$root/switch-commands.jsonl"){@(Get-Content "$root/switch-commands.jsonl").Count}else{0}
 @{pid=$testPid;switches=$count;seconds=$watch.Elapsed.TotalSeconds;traceStarted=$traceStarted;previewClosedWithHome=$true;updatedModule=[bool]$UpdateModule} | ConvertTo-Json > "$root/run.json"
}
