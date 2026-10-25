"""Record hashes and embedded loader config for the locally generated experiment APKs."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

paths = [
    ".tools/lspatch-v1.2-487-debug.jar",
    ".tools/patched-empty/app-debug-487-lspatched.apk",
    "app/build/outputs/apk/debug/app-debug.apk",
    "consumer-test/build/outputs/apk/debug/consumer-test-debug.apk",
    ".tools/patched-consumer/consumer-test-debug-487-lspatched.apk",
    "test-fixtures/A.mp4", "test-fixtures/B.mp4", "camera-bridge/libs/api-82.jar",
]
inventory = {}
for name in paths:
    path = Path(name)
    inventory[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
    if "patched" in name:
        with ZipFile(path) as archive:
            # LSPatch deliberately links entries into its nested origin APK. Permit that overlap
            # only for this known origin entry in the locally generated, bounded-size APK.
            origin = archive.getinfo("assets/lspatch/origin.apk")
            assert origin.file_size < 10_000_000
            origin._end_offset = None
            inventory[name].update(
                config=json.loads(archive.read("assets/lspatch/config.json")),
                embedded_origin_sha256=hashlib.sha256(archive.read(origin)).hexdigest(),
                embedded_modules=[entry for entry in archive.namelist() if entry.startswith("assets/lspatch/modules/")],
            )
Path("test-results/phase2/artifact-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
