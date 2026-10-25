"""Validate a module candidate only. Does not patch/install any target APK."""
import hashlib,json,shutil,subprocess,zipfile
from pathlib import Path
P=Path('test-results/phase18');T=Path('.tools/phase18')
module=Path('app/build/outputs/apk/debug/app-debug.apk')
with zipfile.ZipFile(module) as z:
    assert z.read('assets/xposed_init').decode().strip()=='co.sarae.camera.poc.HookEntry'
    native=[n for n in z.namelist() if n.startswith('lib/') and n.endswith('.so')]
    assert native==['lib/arm64-v8a/libsarae_native_observer.so']
    so=z.read(native[0]);(T/'libsarae_native_observer.so').write_bytes(so)
before=(T/'observer-before.c').read_bytes();after=Path('native-observer/src/main/cpp/observer.c').read_bytes()
assert after.startswith(before),'Phase17 callback implementation changed'
assert b'NativeObservation_diagnostics' in after[len(before):]
old_sources=json.loads(Path('test-results/phase15/source-sha256.json').read_text())
pcm=[(name,digest) for name,digest in old_sources.items() if name.replace('\\','/').startswith('audio-engine/')]
assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest for name,digest in pcm)
for command,name in [(['D:/window/Android/Sdk/build-tools/34.0.0/apksigner.bat','verify','--verbose','--print-certs',str(module)],'candidate-signing.txt'),
                     (['D:/window/Android/Sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-readelf.exe','-h','-d','-n',str(T/'libsarae_native_observer.so')],'observer-elf.txt')]:
    result=subprocess.run(command,capture_output=True,timeout=20,check=True);(P/name).write_bytes(result.stdout)
shutil.copy2(module,T/'module.apk')
result={'staticPass':True,'moduleSha256':hashlib.sha256(module.read_bytes()).hexdigest(),'observerSoSha256':hashlib.sha256(so).hexdigest(),
        'phase17NativeCodeUnchangedPrefix':True,'newNativeFunction':'read-only JNI diagnostics delegates existing global_stats',
        'phase15PcmSourceFilesChecked':len(pcm),'nativeInterceptionAdded':False,'pcmReplacementAdded':False,
        'candidatePath':str(T/'module.apk'),'deploymentRequires':'fresh exact rollback hash and HOME/camera/recording idle guards'}
(P/'candidate.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
