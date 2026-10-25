"""Extract platform method references only. Static presence is NOT runtime invocation evidence."""
import hashlib
import json
import struct
import sys
from pathlib import Path
from zipfile import ZipFile

path=Path(sys.argv[1])
out={"apk_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"bytes":path.stat().st_size,
     "scope":"Base APK only; dynamic splits/native paths not exhaustively analyzed; references do not prove runtime use", "dex":[]}
with ZipFile(path) as apk:
    assert "AndroidManifest.xml" in apk.namelist(), "Incomplete APK (an embedded ZIP can appear valid after interrupted transfer)"
    for name in apk.namelist():
        if not name.endswith('.dex'): continue
        data=apk.read(name)
        assert data[:4]==b'dex\x0a'
        def u32(at): return struct.unpack_from('<I',data,at)[0]
        def table(at): return u32(at),u32(at+4)
        strings_count,strings_offset=table(0x38)
        types_count,types_offset=table(0x40)
        _,protos_offset=table(0x48)
        methods_count,methods_offset=table(0x58)
        cache={}
        def string(index):
            if index not in cache:
                offset=u32(strings_offset+4*index)
                while data[offset]&0x80: offset+=1
                offset+=1
                cache[index]=data[offset:data.index(b'\0',offset)].decode('utf-8','replace')
            return cache[index]
        def typ(index): return string(u32(types_offset+4*index))
        def proto(index):
            offset=protos_offset+index*12
            params=u32(offset+8)
            args=[typ(struct.unpack_from('<H',data,params+4+i*2)[0]) for i in range(u32(params))] if params else []
            return '('+''.join(args)+')'+typ(u32(offset+4))
        refs=[]
        for index in range(methods_count):
            cls,signature,method=struct.unpack_from('<HHI',data,methods_offset+8*index)
            owner=typ(cls)
            if owner.startswith(('Landroid/hardware/camera2/','Landroid/hardware/Camera','Landroid/media/AudioRecord')):
                refs.append({"owner":owner,"method":string(method),"signature":proto(signature)})
        if refs: out['dex'].append({"name":name,"method_references":refs})
Path('test-results/phase4/static-camera-api-refs.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print('dex_with_camera_audio_refs',len(out['dex']))
for dex in out['dex']:
    for ref in dex['method_references']:
        if ref['method']=='createCaptureSession': print(dex['name'],ref)
