"""Bounded offline identity check, no new target analysis or interception."""
import hashlib,json,struct,subprocess,zipfile
from pathlib import Path
P=Path('test-results/phase18');T=Path('.tools/phase18')
fresh=(T/'installed-arm64.apk').exists()
source=T/'installed-arm64.apk' if fresh else Path('.tools/phase17/installed-target-arm64.apk')
with zipfile.ZipFile(source) as z:
    name=next(n for n in z.namelist() if n.endswith('/liblyrax.so'));data=z.read(name)
sha=hashlib.sha256(data).hexdigest()
assert sha=='9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de'
target=T/'liblyrax.so';target.write_bytes(data)
llvm='D:/window/Android/Sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/'
notes=subprocess.run([llvm+'llvm-readelf.exe','-n',str(target)],capture_output=True,timeout=8,check=True).stdout.decode()
(P/'target-build-id.txt').write_text(notes)
build='800d53bec62ddf0a1774842f5a16e1693ab7f0de';assert build in notes
off=struct.unpack_from('<Q',data,40)[0];size,count=struct.unpack_from('<HH',data,58)
sections=[struct.unpack_from('<IIQQQQIIQQ',data,off+i*size) for i in range(count)]
words=json.loads(Path('test-results/phase16/lyrax-targeted.json').read_text())['registrationCandidate']['verifiedInstructionWords']
for address,expected in words.items():
    va=int(address,16);section=next(s for s in sections if s[1]!=8 and s[3]<=va<s[3]+s[5])
    assert struct.unpack_from('<I',data,section[4]+va-section[3])[0]==int(expected,16)
with zipfile.ZipFile(T/'module.apk') as z:
    assert 'assets/native_init' not in z.namelist()
symbols=subprocess.run([llvm+'llvm-nm.exe','-D',str(T/'libsarae_native_observer.so')],capture_output=True,timeout=8,check=True).stdout.decode()
assert ' native_init' not in symbols and 'DobbyHook' not in symbols
(P/'observer-exports.txt').write_text(symbols)
result={'freshInstalledSplitVerified':fresh,'source':str(source),'sha256':sha,'buildId':build,'verifiedInstructionWords':words,
        'callbackCandidateRva':'0x6be020','targetInterceptionInstalled':False,'registrationObserved':None,
        'noNativeInitAssetOrExport':True,'noDobbyHookSymbol':True}
(P/'target-identity.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
