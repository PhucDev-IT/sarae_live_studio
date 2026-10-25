"""Host-side ADB deadline: a disconnected transport must not consume the test budget."""
import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--timeout',type=float,default=12)
p.add_argument('--journal',type=Path,required=True)
p.add_argument('--stdin-file',type=Path)
p.add_argument('arguments',nargs=argparse.REMAINDER)
a=p.parse_args()
arguments=a.arguments[1:] if a.arguments[:1]==['--'] else a.arguments
if not arguments: p.error('ADB arguments required')
start=time.monotonic()
record={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'arguments':arguments,'timeout_seconds':a.timeout}
try:
    result=subprocess.run(['D:/window/Android/Sdk/platform-tools/adb.exe',*arguments],capture_output=True,timeout=a.timeout,
                          input=a.stdin_file.read_bytes() if a.stdin_file else None,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr)
    record['exit_code']=result.returncode
except subprocess.TimeoutExpired as e:
    if e.stdout: sys.stdout.buffer.write(e.stdout)
    if e.stderr: sys.stderr.buffer.write(e.stderr)
    sys.stderr.write(f'\nADB host timeout after {a.timeout}s; no retry performed.\n')
    record['exit_code']=124
    record['timed_out']=True
finally:
    record['seconds']=time.monotonic()-start
    with a.journal.open('a',encoding='utf-8') as f: f.write(json.dumps(record)+'\n')
raise SystemExit(record['exit_code'])
