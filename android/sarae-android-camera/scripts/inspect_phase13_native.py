"""Read-only ELF dependency/symbol inspection of the preserved original APK set."""
import hashlib
import json
import re
import struct
from pathlib import Path
from zipfile import ZipFile

OUT=Path('test-results/phase13');OUT.mkdir(exist_ok=True)
ROOT=Path('.tools/phase6/original')
API=re.compile(r'AAudio|slCreateEngine|SL_IID_(?:RECORD|ANDROIDSIMPLEBUFFERQUEUE|BUFFERQUEUE|ENGINE)|AudioRecord|OpenSLES|opensles|AudioRecorder|audio.?capture',re.I)

def elf(blob):
    assert blob[:6]==b'\x7fELF\x02\x01'
    shoff=struct.unpack_from('<Q',blob,40)[0]
    shentsize,shnum,shstr=struct.unpack_from('<HHH',blob,58)
    headers=[struct.unpack_from('<IIQQQQIIQQ',blob,shoff+i*shentsize) for i in range(shnum)]
    def section(i):
        h=headers[i];return blob[h[4]:h[4]+h[5]]
    def string(table,index):return table[index:table.index(b'\0',index)].decode(errors='replace')
    names=section(shstr)
    result={'machine':struct.unpack_from('<H',blob,18)[0],'needed':[],'symbols':[]}
    for h in headers:
        if h[1] not in (6,11):continue
        strings=section(h[6]);data=blob[h[4]:h[4]+h[5]]
        if h[1]==6:
            for offset in range(0,len(data),h[9] or 16):
                tag,value=struct.unpack_from('<qQ',data,offset)
                if tag==1:result['needed'].append(string(strings,value))
        else:
            for offset in range(0,len(data),h[9] or 24):
                name,info,other,index,value,size=struct.unpack_from('<IBBHQQ',data,offset)
                name=string(strings,name)
                if API.search(name):result['symbols'].append({'name':name,'imported':index==0,'value':hex(value),'size':size})
    result['api_strings']=sorted(set(s.decode(errors='replace') for s in re.findall(rb'[\x20-\x7e]{6,200}',blob) if API.search(s.decode(errors='replace'))))[:100]
    return result

expected={x['name']:x for x in json.loads(Path('test-results/phase6/original-apks.json').read_text(encoding='utf-8-sig'))}
records=[];scanned=0;archives=[]
for p in sorted(ROOT.glob('*.apk')):
    sha=hashlib.sha256(p.read_bytes()).hexdigest();assert sha==expected[p.name]['sha256']
    archives.append({'name':p.name,'sha256':sha})
    with ZipFile(p) as z:
        for entry in z.infolist():
            if not entry.filename.endswith('.so'):continue
            blob=z.read(entry)
            if not blob.startswith(b'\x7fELF\x02\x01'):continue
            scanned+=1
            value=elf(blob)
            if value['symbols'] or any(API.search(x) for x in value['needed']):
                records.append({'apk':p.name,'entry':entry.filename,'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest(),**value})
summary={'scope':'Original APK snapshots only; dependencies and symbols do NOT prove LIVE runtime invocation. Downloaded libraries may be absent.',
         'apk_count':len(archives),'elf_scanned':scanned,'archives':archives,'candidates':records}
(OUT/'native-static.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({'apk_count':len(archives),'elf_scanned':scanned,'candidates':[{k:r[k] for k in ('apk','entry','needed','symbols')} for r in records]},indent=2))
