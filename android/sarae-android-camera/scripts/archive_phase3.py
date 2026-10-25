"""Losslessly compress complete raw evidence and record hashes for source/build/test artifacts."""
import gzip
import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

directory=Path("test-results/phase3/endurance")
inventory={}
def record(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            digest.update(block)
    inventory[str(path)]={"sha256":digest.hexdigest(),"bytes":path.stat().st_size}

for name in ("bridge.jsonl","consumer.jsonl","pid-logcat.txt"):
    path=directory/name
    record(path)
    compressed=path.with_name(path.name+".gz")
    with path.open("rb") as source, compressed.open("wb") as target:
        with gzip.GzipFile(filename="",mode="wb",fileobj=target,mtime=0) as archive:
            shutil.copyfileobj(source,archive)
    record(compressed)
for name in ("app/build/outputs/apk/debug/app-debug.apk",
             "consumer-test/build/outputs/apk/debug/consumer-test-debug.apk",
             ".tools/phase3-consumer/consumer-test-debug-487-lspatched.apk",
             ".tools/phase3-lifecycle-apk/consumer-test-debug-487-lspatched.apk",
             ".tools/lspatch-v1.2-487-debug.jar","test-fixtures/A.mp4","test-fixtures/B-phase3.mp4"):
    record(Path(name))
for module in ('engine','camera-bridge','consumer-test'):
    for path in Path(module+'/src/main').rglob('*.kt'):
        record(path)
record(Path('app/src/main/java/co/sarae/camera/poc/HookEntry.kt'))
with ZipFile(".tools/phase3-consumer/consumer-test-debug-487-lspatched.apk") as apk:
    inventory["loader_config"]=json.loads(apk.read("assets/lspatch/config.json"))
Path("test-results/phase3/artifact-inventory.json").write_text(json.dumps(inventory,indent=2),encoding="utf-8")
