"""One bounded static pass rooted in Phase 14's known recorder strings.

No APK-wide scan; no runtime hooks or ABI guessed from strings. ADRP/ADD
matches are candidates, not verified function boundaries or executed calls.
"""
import hashlib,json,struct,subprocess
from pathlib import Path

P=Path('test-results/phase16');T=Path('.tools/phase16');P.mkdir(exist_ok=True);T.mkdir(exist_ok=True)
binary=Path('.tools/phase14/liblyrax.so');data=binary.read_bytes()
sha=hashlib.sha256(data).hexdigest()
assert sha=='9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de'
assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
off=struct.unpack_from('<Q',data,40)[0]
size,count,strings=struct.unpack_from('<HHH',data,58)
sections=[struct.unpack_from('<IIQQQQIIQQ',data,off+i*size) for i in range(count)]
def section(h):return data[h[4]:h[4]+h[5]]
def string(table,n):return table[n:table.index(b'\0',n)].decode(errors='replace')
names=section(sections[strings])
def va(file_offset):
    h=next(h for h in sections if h[1]!=8 and h[4]<=file_offset<h[4]+h[5])
    return h[3]+file_offset-h[4]
needles=[b'(*simple_buffer_queue_)->RegisterCallback(simple_buffer_queue_, SimpleBufferQueueCallback, this)',
         b'GetInterface SL_IID_RECORD failed: ',b'AudioRecordOpenSLES',b'audio_record_opensles.cc']
targets=[]
for needle in needles:
    start=0
    while (at:=data.find(needle,start))>=0:
        targets.append({'text':needle.decode(),'fileOffset':at,'va':va(at),'bytes':len(needle)});start=at+len(needle)
hits=[]
for h in sections:
    if not h[2]&4:continue
    code=section(h)
    for offset in range(0,len(code)-24,4):
        word=struct.unpack_from('<I',code,offset)[0]
        if word&0x9f000000!=0x90000000:continue
        reg=word&31;imm=((word>>5)&0x7ffff)<<2|((word>>29)&3)
        if imm&(1<<20):imm-=1<<21
        pc=h[3]+offset;page=(pc&~4095)+(imm<<12)
        for distance in range(1,5):
            add=struct.unpack_from('<I',code,offset+distance*4)[0]
            if add&0xff000000!=0x91000000 or ((add>>5)&31)!=reg:continue
            address=page+((add>>10)&4095)*(4096 if add&(1<<22) else 1)
            for target in targets:
                if target['va']<=address<target['va']+target['bytes']:
                    hits.append({'adrp':hex(pc),'add':hex(pc+distance*4),'materializedAddress':hex(address),
                        'string':target['text'],'section':string(names,h[0]),'confidence':'static candidate only; no execution or callback-pointer proof'})
exports=[]
for h in sections:
    if h[1]!=11:continue
    table=section(sections[h[6]]);symbols=section(h)
    for at in range(0,len(symbols),h[9] or 24):
        n,info,other,index,value,length=struct.unpack_from('<IBBHQQ',symbols,at)
        name=string(table,n)
        if name.startswith('Java_com_ss_ttlivestreamer_') and any(x in name for x in ['nativeSetRecordingCallback','nativeStartRecording','nativeAudioRecordWritePcm','nativeAudioRecordInit']):
            exports.append({'symbol':name,'value':hex(value),'bytes':length})
result={'scope':'One preserved liblyrax, known recorder string roots only; bounded one pass, then STOP',
        'sha256':sha,'targets':[{**t,'va':hex(t['va'])} for t in targets],
        'staticAddressMaterializationCandidates':hits,'jniCandidates':exports,
        'runtimeBackend':'SUSPECTED_OPENSL_ES','runtimePcmEntry':'UNKNOWN',
        'stopReason':'Static references cannot establish selected backend, callback registration/context, PCM ownership or encoder handoff'}
def word_at(address):
    h=next(h for h in sections if h[1]!=8 and h[3]<=address<h[3]+h[5])
    return struct.unpack_from('<I',data,h[4]+address-h[3])[0]
# Verify the one candidate reached from the known RegisterCallback assertion.
# This is static instruction decoding, never an address to deploy blindly.
expected={0x6bdaa0:0xf9406e60,0x6bdaa4:0xb0000001,0x6bdaa8:0x91008021,
          0x6bdaac:0xaa1303e2,0x6bdab0:0xf9400008,0x6bdab4:0x941c8170,
          0xdde074:0xf9400d08,0xdde078:0xd61f0100}
assert all(word_at(address)==instruction for address,instruction in expected.items())
result['registrationCandidate']={
    'status':'STATIC_ONLY; actual LIVE invocation UNKNOWN',
    'callSiteRva':'0x6bdab4','callbackCandidateRva':'0x6be020','outlinedThunkRva':'0xdde074',
    'queueArgument':'x0 loaded from [x19+0xd8]','contextArgument':'x2=x19',
    'callbackArgument':'x1=ADRP(0x6be000)+0x20',
    'indirectVtableOffset':'0x18: fourth pointer, RegisterCallback in ARM64 Android simple buffer queue header',
    'verifiedInstructionWords':{hex(address):hex(instruction) for address,instruction in expected.items()},
    'notEstablished':['LIVE selected backend','runtime registration success','recorder and queue ownership','buffer addresses/sizes','PCM handoff to encoder']}
objdump='D:/window/Android/Sdk/ndk/28.2.13676358/toolchains/llvm/prebuilt/windows-x86_64/bin/llvm-objdump.exe'
with (T/'known-string-xrefs-disasm.txt').open('wb') as output:
    for hit in hits[:8]:
        pc=int(hit['adrp'],16)
        r=subprocess.run([objdump,'-d',f'--start-address={pc-24}',f'--stop-address={pc+80}',str(binary)],capture_output=True,timeout=10,check=True)
        output.write(r.stdout)
(P/'lyrax-targeted.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
