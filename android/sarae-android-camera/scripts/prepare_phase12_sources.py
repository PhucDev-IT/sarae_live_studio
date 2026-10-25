"""Replace only engine fixtures before the sole LIVE. No app/module/lifecycle changes."""
import hashlib,json,subprocess,time,xml.etree.ElementTree as ET
from pathlib import Path
P=Path('test-results/phase12');S=P/'state.json';s=json.loads(S.read_text())
assert s['session_count']==0 and not s['live_active'] and not s['finished'],'Never replace source files during LIVE'
assert not s.get('new_sources_installed'),'Preserve this execution; do not overwrite backups'
start=time.monotonic();PKG='com.ss.android.ugc.trill';FILES=f'/sdcard/Android/data/{PKG}/files'
sources={'A':Path('C:/Users/nguye/Downloads/c56c3dcfeb91499d970d953604771f88.mp4'),
         'B':Path('C:/Users/nguye/Downloads/5baed9ab52bf4730a3db889c3707dd12.mp4')}
manifest={k:{'local_path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size,
             'metadata':json.loads((P/f'source-{k}-new-ffprobe.json').read_text(encoding='utf-8-sig'))} for k,p in sources.items()}
def adb(args,name=None,timeout=5):
    remaining=250-s['spent_seconds']-(time.monotonic()-start)
    assert remaining>1,'Protect LIVE and cleanup budget'
    begin=time.monotonic();r=subprocess.run(['D:/window/Android/Sdk/platform-tools/adb.exe',*args],capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
    with (P/'adb.jsonl').open('a') as f:f.write(json.dumps({'stage':'new-sources-before-live','args':args,'seconds':time.monotonic()-begin,'exit_code':r.returncode})+'\n')
    if name:(P/name).write_bytes(r.stdout)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace'))
    return r.stdout.decode(errors='replace')
def snapshot(k):
    pid=adb(['shell','pidof',PKG]).strip();assert pid==s['pid'],'PID changed; no lifecycle intervention'
    for phase,name in [('phase11','audio'),('phase6','bridge')]:
        prefix='audio' if name=='audio' else 'bridge'
        adb(['pull',f'{FILES}/{phase}/{prefix}-{pid}.jsonl',str(P/f'new-source-{k}-{name}.jsonl')],timeout=5)
    adb(['shell','screencap','-p','/sdcard/sarae-phase12-new.png'])
    adb(['pull','/sdcard/sarae-phase12-new.png',str(P/f'new-source-{k}.png')])
try:
    s['preview_pass']=False;S.write_text(json.dumps(s,indent=2))
    adb(['shell','timeout','8','uiautomator','dump','--compressed','/sdcard/sarae-phase12-sources.xml'],'source-ui-dump.txt',10)
    adb(['pull','/sdcard/sarae-phase12-sources.xml',str(P/'source-ui.xml')])
    tree=ET.parse(P/'source-ui.xml')
    if not any(n.get('package')==PKG for n in tree.iter('node')):
        # Bring the existing app forward from Gallery; never force-stop/restart it.
        adb(['shell','am','start','-W','-n',PKG+'/com.ss.android.ugc.aweme.splash.SplashActivity'],'return-to-tiktok.txt',10)
        time.sleep(1)
        adb(['shell','timeout','8','uiautomator','dump','--compressed','/sdcard/sarae-phase12-sources.xml'],'source-ui-dump.txt',10)
        adb(['pull','/sdcard/sarae-phase12-sources.xml',str(P/'source-ui.xml')])
    def dump_ui():
        adb(['shell','timeout','8','uiautomator','dump','--compressed','/sdcard/sarae-phase12-sources.xml'],'source-ui-dump.txt',10)
        adb(['pull','/sdcard/sarae-phase12-sources.xml',str(P/'source-ui.xml')])
        return ET.parse(P/'source-ui.xml')
    tree=ET.parse(P/'source-ui.xml')
    if not any(n.get('text')=='Phát LIVE' for n in tree.iter('node')):
        create=next((n for n in tree.iter('node') if n.get('resource-id')==PKG+':id/ond' and n.get('content-desc')=='Quay'),None)
        if create is not None:
            import re
            x1,y1,x2,y2=map(int,re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',create.get('bounds')).groups())
            adb(['shell','input','tap',str((x1+x2)//2),str((y1+y2)//2)])
            time.sleep(1);tree=dump_ui()
        if not any(n.get('text')=='Phát LIVE' for n in tree.iter('node')):
            tab=next((n for n in tree.iter('node') if n.get('text')=='LIVE' and n.get('package')==PKG),None)
            if tab is not None:
                import re
                x1,y1,x2,y2=map(int,re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',tab.get('bounds')).groups())
                adb(['shell','input','tap',str((x1+x2)//2),str((y1+y2)//2)])
                time.sleep(1);tree=dump_ui()
    assert any(n.get('text')=='Phát LIVE' for n in ET.parse(P/'source-ui.xml').iter('node')),'Not pre-LIVE; do not replace files'
    for key,path in sources.items():
        remote=f'{FILES}/phase12-{key}.upload'
        adb(['push',str(path),remote],f'push-new-{key}.txt',20)
        assert adb(['shell','sha256sum',remote],f'upload-new-{key}-sha256.txt').split()[0]==manifest[key]['sha256']
    # Rename within the engine directory: old open decoder FDs retain their original inode.
    for key in sources:
        adb(['shell','mv',f'{FILES}/{key}.mp4',f'{FILES}/phase12-original-{key}.mp4'])
        adb(['shell','mv',f'{FILES}/phase12-{key}.upload',f'{FILES}/{key}.mp4'])
    for key in ['B','A']:
        adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p',PKG,'--es','source',key,'--es','mode','now'],f'preview-new-{key}-switch.txt')
        time.sleep(3);snapshot(key)
    hashes=adb(['shell','sha256sum',f'{FILES}/A.mp4',f'{FILES}/B.mp4'],'new-media-hashes.txt').splitlines()
    assert [x.split()[0] for x in hashes]==[manifest[k]['sha256'] for k in ['A','B']]
    (P/'source-media.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    s['new_sources_installed']=True;s['preview_switches_new_media']=2
finally:
    duration=time.monotonic()-start;s['spent_seconds']+=duration;S.write_text(json.dumps(s,indent=2))
    with (P/'budget.jsonl').open('a') as f:f.write(json.dumps({'stage':'new-sources-before-live','seconds':duration})+'\n')
    print(json.dumps({'active_device_seconds':s['spent_seconds'],'new_sources_installed':s.get('new_sources_installed',False),'LIVE_sessions':s['session_count']},indent=2),flush=True)
