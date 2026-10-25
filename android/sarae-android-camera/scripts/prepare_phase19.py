"""Build artifacts must exist. Patch only the owned harness, never TikTok."""
import hashlib,json,subprocess,zipfile
from pathlib import Path
P=Path('test-results/phase19');T=Path('.tools/phase19');P.mkdir(exist_ok=True);T.mkdir(exist_ok=True)
JAR=Path('.tools/lspatch-v1.2-487-debug.jar')
assert hashlib.sha256(JAR.read_bytes()).hexdigest()=='5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153'
JAVA='D:/window/Eclipse Adoptium/jdk-21.0.12.101-hotspot/bin/java.exe'
module=Path('native-observer-module/build/outputs/apk/debug/native-observer-module-debug.apk')
original=Path('native-audio-test/build/outputs/apk/debug/native-audio-test-debug.apk')
output=T/'patched';output.mkdir(exist_ok=True)
if not list(output.glob('*lspatched.apk')):
    with (T/'patch.txt').open('wb') as f:
        subprocess.run([JAVA,'-jar',str(JAR),'-d','-l','0','-m',str(module),'-k','C:/Users/nguye/.android/debug.keystore','android','androiddebugkey','android','-o',str(output),str(original)],stdout=f,stderr=subprocess.STDOUT,timeout=120,check=True)
patched=next(output.glob('*lspatched.apk'))
with zipfile.ZipFile(patched) as z:
    config=json.loads(z.read('assets/lspatch/config.json'))
    assert config['sigBypassLevel']==0 and config['lspConfig']['VERSION_CODE']==487
    assert config['useManager']==False and config['injectDex']==False
    origin=z.getinfo('assets/lspatch/origin.apk')
    # Pinned LSPatch intentionally aliases ZIP regions. Only read this known,
    # locally generated entry, bounded by our original APK size and exact hash.
    assert origin.file_size==original.stat().st_size
    origin._end_offset=None
    assert hashlib.sha256(z.read(origin)).digest()==hashlib.sha256(original.read_bytes()).digest()
    modules=[n for n in z.namelist() if n.startswith('assets/lspatch/modules/')]
    assert len(modules)==1 and hashlib.sha256(z.read(modules[0])).digest()==hashlib.sha256(module.read_bytes()).digest()
with zipfile.ZipFile(original) as z:assert not any('native_observer.so' in n for n in z.namelist()),'Observer must come from the module, not the host APK'
with zipfile.ZipFile(module) as z:
    assert z.read('assets/native_init').decode().strip()=='libsarae_hook_validation.so'
    hook_so=z.read('lib/arm64-v8a/libsarae_hook_validation.so')
    (T/'libsarae_hook_validation.so').write_bytes(hook_so)
    so=z.read('lib/arm64-v8a/libsarae_native_observer.so')
    (T/'libsarae_native_observer.so').write_bytes(so)
readelf='D:/window/Android/Sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-readelf.exe'
for name,flags,path in [('observer-elf.txt',['-h','-d','-n'],T/'libsarae_native_observer.so'),('hook-elf.txt',['-h','-d','-n'],T/'libsarae_hook_validation.so')]:
    r=subprocess.run([readelf,*flags,str(path)],capture_output=True,timeout=10,check=True);(P/name).write_bytes(r.stdout)
for flag,name in [(['verify','--verbose','--print-certs'],'signing.txt')]:
    r=subprocess.run(['D:/window/Android/Sdk/build-tools/34.0.0/apksigner.bat',*flag,str(patched)],capture_output=True,timeout=30,check=True)
    (P/name).write_bytes(r.stdout)
result={'loaderJarSha256':hashlib.sha256(JAR.read_bytes()).hexdigest(),'patchedHarness':str(patched),'config':config,
        'moduleSha256':hashlib.sha256(module.read_bytes()).hexdigest(),'originalHarnessSha256':hashlib.sha256(original.read_bytes()).hexdigest(),
        'patchedHarnessSha256':hashlib.sha256(patched.read_bytes()).hexdigest(),'observerSoSha256':hashlib.sha256(so).hexdigest(),
        'hookSoSha256':hashlib.sha256(hook_so).hexdigest(),'observerAbsentFromHostApk':True,'targetPatched':'co.sarae.camera.nativeaudiotest only; not TikTok'}
(P/'artifacts.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
