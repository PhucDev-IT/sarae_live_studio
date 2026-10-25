"""Verify the exact installed APK set offline before replacing the authorized lab app."""
import concurrent.futures
import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

root=Path('test-results/phase6')
parser=argparse.ArgumentParser()
parser.add_argument('--stage',default='original',choices=['original','loader-only','bridge'])
stage=parser.parse_args().stage
original=Path('.tools/phase6')/stage
sdk=Path('D:/window/Android/Sdk/build-tools/34.0.0')
records=json.loads((root/'original-apks.json').read_text(encoding='utf-8-sig'))
out=root/f'{stage}-static'
out.mkdir(exist_ok=True)

def command(args):
    result=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace')
    if result.returncode: raise RuntimeError(result.stdout)
    return result.stdout

def inspect(record):
    name=record['name'] if stage=='original' else record['name'].replace('.apk','-487-lspatched.apk')
    path=original/name
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if stage=='original': assert digest==record['sha256']
    config=None
    with zipfile.ZipFile(path) as archive:
        assert 'AndroidManifest.xml' in archive.namelist()
        if stage!='original':
            # Locally generated LSPatch files intentionally link entries into nested origin.
            # Permit overlap only for that bounded entry and match its full original hash.
            origin=archive.getinfo('assets/lspatch/origin.apk')
            assert origin.file_size==record['bytes'] and origin.file_size<300_000_000
            origin._end_offset=None
            assert hashlib.sha256(archive.read(origin)).hexdigest()==record['sha256']
            modules=[n for n in archive.namelist() if n.startswith('assets/lspatch/modules/')]
            if record['name']=='base.apk':
                config=json.loads(archive.read('assets/lspatch/config.json'))
                assert config['sigBypassLevel']==0 and not config['useManager']
                assert config['lspConfig']['VERSION_CODE']==487
                assert bool(modules)==(stage=='bridge')
            else:
                assert not modules and 'assets/lspatch/config.json' not in archive.namelist()
        assert archive.testzip() is None
        dex=[n for n in archive.namelist() if re.fullmatch(r'classes\d*\.dex',n)]
        abis=sorted({n.split('/')[1] for n in archive.namelist() if n.startswith('lib/') and n.endswith('.so')})
    badging=command([sdk/'aapt2.exe','dump','badging',path])
    manifest=command([sdk/'aapt2.exe','dump','xmltree','--file','AndroidManifest.xml',path])
    # Verify the actual API 34 installation target. Repacked splits retain minSdk 23
    # but LSPatch emits v2 signatures and the loader base itself requires API >=28.
    signing=command([sdk/'apksigner.bat','verify','--verbose','--print-certs','--min-sdk-version','34',path])
    for kind,content in [('badging',badging),('manifest',manifest),('signing',signing)]:
        (out/f'{path.stem}-{kind}.txt').write_text(content,encoding='utf-8')
    package=re.search(r"package: name='([^']+)' versionCode='([^']+)'",badging)
    assert package and package[1]=='com.ss.android.ugc.trill' and package[2]=='470403',badging[:300]
    if record['name']=='base.apk': assert "versionName='47.4.3'" in badging
    certificate=re.search(r'Signer #1 certificate SHA-256 digest: (\w+)',signing)[1]
    split=re.search(r"\bsplit='([^']+)'",badging)
    dependencies=re.findall(r'E: uses-split[^\n]*\n\s*A: [^\n=]*:name[^\n=]*="([^"]+)"',manifest)
    config_for=re.search(r'A: [^\n=]*\bconfigForSplit[^\n=]*="([^"]+)"',manifest)
    return {**record,'name':name,'original_sha256':record['sha256'],'sha256':digest,'bytes':path.stat().st_size,'loader_config':config,
            'zip_crc':'PASS','package':package[1],'version_code':int(package[2]),'split':split[1] if split else None,
            'config_for_split':config_for[1] if config_for else None,'uses_splits':dependencies,'dex_files':len(dex),
            'native_abis':abis,'certificate_sha256':certificate}

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    inspected=list(pool.map(inspect,records))
names={r['split'] for r in inspected if r['split']}
assert len(names)==len(inspected)-1
for r in inspected:
    assert all(s in names for s in r['uses_splits']),r
    assert not r['config_for_split'] or r['config_for_split'] in names,r
expected_certificate='d7811ec4166fea6cc720ba66699dc84b584ac9e6986613a76d4e43d8cbe32b27' if stage=='original' else '8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b'
assert {r['certificate_sha256'] for r in inspected}=={expected_certificate}
result={'result':'PASS','apk_count':len(inspected),'total_bytes':sum(r['bytes'] for r in inspected),
        'scope':'All 61 currently installed APKs; future on-demand delivery not guaranteed',
        'apks':inspected}
(root/f'{stage}-static.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='apks'},indent=2))
