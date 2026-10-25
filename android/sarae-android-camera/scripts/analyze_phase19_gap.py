"""Existing Phase18 event timeline only; never drive a new camera run."""
import json
from pathlib import Path
P=Path('test-results/phase19');root=Path('test-results/phase18')
rows=[json.loads(line) for line in (root/'final-bridge.jsonl').read_text().splitlines()]
out=[e for e in rows if e['kind']=='output_present']
i=max(range(1,len(out)),key=lambda i:out[i]['emittedNs']-out[i-1]['emittedNs'])
before,after=out[i-1:i+1];t=after['timingsNs'];start=t['tickStartNs'];end=after['emittedNs']
near=[e for e in rows if before['emittedNs']-100_000_000<=e.get('emittedNs',e['ns'])<=end+80_000_000]
rendered=[e for e in near if e['kind']=='frame_rendered' and start<=e['emittedNs']<=end]
ticks=[e for e in near if e['kind']=='decoder_tick' and start<=e['emittedNs']<=end]
native=[json.loads(line) for line in (root/'final-native.jsonl').read_text().splitlines()]
complete=next(e for e in native if e['event']=='observation_worker_complete')
result={'eventGapMs':(after['emittedNs']-before['emittedNs'])/1e6,
    'rendererSwapGapMs':int(after['detail'].split('gapNs=')[1].split()[0])/1e6,
    'before':before,'after':after,'decompositionMs':{key:value/1e6 for key,value in t.items() if key in ['tickLateNs','activeLatchNs','pendingLatchNs','drawNs','swapNs','tickToPresentNs','previousOutputLogNs']},
    'tickStartGapMs':(start-before['timingsNs']['tickStartNs'])/1e6,
    'decoderRenderedDuringSlowTick':len(rendered),'decoderTicksEmittedDuringSlowTick':len(ticks),
    'maxDecoderWorkerMsDuringSlowTick':max(e['timingsNs']['workerDurationNs'] for e in ticks)/1e6,
    'maxFrameRenderedHandlerReceiptDelayMs':max(e['receivedNs']-e['emittedNs'] for e in rendered)/1e6,
    'observerWorkerCompletedSecondsBeforeSlowTick':(start-complete['emissionNs'])/1e9,
    'switchesInWindow':[e for e in near if e['kind'] in ['switch_request','switch_start','switch_complete']],
    'confirmed':'138.032239 ms wall time is localized to active latch() scope, rather than a late tick start; decoder worker continues emitting frames',
    'suspected':'SurfaceTexture/GL latch/fence/driver work or descheduling inside the latch scope',
    'unknown':'Which latch sub-operation or off-CPU wait consumed the interval; no CPU scheduling/fence trace in Phase18',
    'limits':['latch() includes updateTexImage, transform/timestamp access and source_latched event; not just updateTexImage',
        'Decoder event emission and renderer handler receipt must not be conflated',
        'No observer startup overlaps the gap; this does not establish a counterfactual overhead bound',
        'No EGL or VideoEngine changes justified from this trace alone']}
(P/'phase18-gap-analysis.json').write_text(json.dumps(result,indent=2))
(P/'phase18-gap-timeline.json').write_text(json.dumps(near,indent=2))
print(json.dumps({key:value for key,value in result.items() if key not in ['before','after']},indent=2))
