# Phase 18 rollback

Before deployment a fresh copy of the cached Phase 14 module was pulled to
`.tools/phase18/rollback-module.apk`, verified SHA256:
`53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`.
An independently preserved copy is `.tools/phase18/expected-rollback-module.apk`.

Only module cache `cache/lspatch/co.sarae.camera.poc/183496786.apk` is replaced;
installed base/splits and account data are not replaced. Deployment first creates
the same-directory `.phase18-rollback` copy and verifies its hash. The candidate
is verified before atomic rename. Fresh backup is a required deployment gate.

While this execution is unfinished, run `python scripts/phase18_device.py rollback`
from this project, using the configured Python/ADB paths. This first returns from
the test preview to HOME, checks idle camera/recording, stops only idle TikTok,
then copies the verified backup through a temporary cache file and atomically
restores it. It does not install, clear data or restart recording.

After execution is marked finished, do not reset the budget/state file to reuse
the script. A future rollback must establish a new idle-state check and device
budget, then restore the same verified artifact to the same cache path. Never
stop active recording, LIVE or other user activity to roll back.

LSPatch cache eviction may restore the original embedded module. A cache update
is not a permanent change to the installed APK. Preserve both local backup APKs
when moving this checkout: `.tools` is ignored by Git.
