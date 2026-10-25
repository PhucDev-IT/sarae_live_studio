param([ValidatePattern('^[a-z0-9-]+$')][string]$StageName='patched')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:JAVA_HOME='D:/window/Eclipse Adoptium/jdk-21.0.12.101-hotspot'
$env:PATH="$env:JAVA_HOME/bin;$env:PATH"
if((Get-FileHash .tools/lspatch-v1.2-487-debug.jar -Algorithm SHA256).Hash.ToLowerInvariant() -ne '5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153'){throw 'Pinned loader mismatch'}
$directory=".tools/phase8/$StageName"
$prefix=if($StageName -eq 'patched'){''}else{"$StageName-"}
if(Test-Path "$directory/base-487-lspatched.apk"){throw 'Preserve existing patched artifact'}
New-Item -ItemType Directory -Force $directory | Out-Null
Copy-Item app/build/outputs/apk/debug/app-debug.apk "$directory/module.apk"
& java.exe -jar .tools/lspatch-v1.2-487-debug.jar -d -l 0 -m "$directory/module.apk" -k C:/Users/nguye/.android/debug.keystore android androiddebugkey android -o $directory .tools/phase6/original/base.apk *> "test-results/phase8/${prefix}patch.txt"
if($LASTEXITCODE -ne 0){throw 'Patching failed'}
& D:/window/Android/Sdk/build-tools/34.0.0/apksigner.bat verify --verbose --print-certs --min-sdk-version 34 "$directory/base-487-lspatched.apk" > "test-results/phase8/${prefix}signing.txt"
if($LASTEXITCODE -ne 0){throw 'APK verification failed'}
& D:/window/Android/Sdk/build-tools/34.0.0/aapt2.exe dump badging "$directory/base-487-lspatched.apk" > "test-results/phase8/${prefix}badging.txt"
if($LASTEXITCODE -ne 0){throw 'Manifest check failed'}
@'
import json,hashlib,zipfile,sys
from pathlib import Path
root=Path('test-results/phase8')
stage=sys.argv[1]; prefix='' if stage=='patched' else stage+'-'
p=Path('.tools/phase8')/stage/'base-487-lspatched.apk'
module=Path('.tools/phase8')/stage/'module.apk'
with zipfile.ZipFile(p) as z:
    origin=z.getinfo('assets/lspatch/origin.apk'); origin._end_offset=None
    assert hashlib.sha256(z.read(origin)).hexdigest()=='80277ad5eaf6eb5d0dcf39537b03274ce417bab8259d661003a6a1fc1ff0f6dd'
    assert z.testzip() is None
    c=json.loads(z.read('assets/lspatch/config.json'))
    assert c['sigBypassLevel']==0 and c['useManager']==False and c['injectDex']==False
    assert c['lspConfig']['VERSION_CODE']==487
    modules=[n for n in z.namelist() if n.startswith('assets/lspatch/modules/')]
    assert len(modules)==1 and hashlib.sha256(z.read(modules[0])).hexdigest()==hashlib.sha256(module.read_bytes()).hexdigest()
signing=(root/(prefix+'signing.txt')).read_text(encoding='utf-8-sig')
assert '8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b' in signing
badging=(root/(prefix+'badging.txt')).read_text(encoding='utf-8-sig')
assert "name='com.ss.android.ugc.trill' versionCode='470403' versionName='47.4.3'" in badging
result={'result':'PASS','base_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),'loader_config':c,'update_mode':'pm install-create -r -p com.ss.android.ugc.trill: replace base, inherit existing 60 splits, retain data','rollback_base':'.tools/phase6/bridge/base-487-lspatched.apk'}
(root/(prefix+'update-static.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
'@ | python - $StageName
if($LASTEXITCODE -ne 0){throw 'Static gates failed'}
