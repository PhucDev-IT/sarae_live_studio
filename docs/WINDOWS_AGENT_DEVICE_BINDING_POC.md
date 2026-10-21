# Windows Agent device binding: audit, PoC, and rollout gate

Status: **PoC only** (2026-10-21). The collector is offline. No activation or recovery endpoint uses its output. A real same-PC Windows reinstall and a different-PC comparison have **not** been completed.

## Current implementation

`ActivationService.activate` sends only one-time code and hostname. `identity.activate` consumes the code and creates a permanent Agent ID plus a random bearer credential. `registered_agents` has one `credential_hash` per Agent, but no device identity, public key, recovery grant, credential generation, or activation/recovery audit. `verify_agent` checks only the bearer hash, owner and license. A copy of the plaintext credential can therefore work on another PC. `deactivate_local` deletes the local credential without revoking Cloud access; after deletion or Windows reinstall, the one-time code cannot recover the old Agent ID. The GUI documentation correctly says the current DPAPI secret is bound to the Windows login. Legacy credential import preserves Agent ID if the old DPAPI blob is decryptable.

The compiled application and config live outside the credential file. An ordinary software update must preserve `%LOCALAPPDATA%\SaraeAgent\data\secrets\agent_credential.dpapi.json`; update/installer tests must verify this. Never create a replacement Agent ID automatically because a file is missing or DPAPI fails.

## PoC collector

`agent-core/src/sarae_agent/activation/device_probe.py` reads SMBIOS UUID, baseboard serial, BIOS serial, Machine GUID, disk 0 serial and TPM availability. Disk 0 is only a diagnostic and is not necessarily the Windows boot disk. The collector suppresses common OEM placeholders. Reports contain HMAC-SHA256 digests using a **random 32-byte study key**, not raw identifiers. Keep that key offline and reuse it across the two captures; a new key makes reports incomparable. Reports and key remain sensitive and should be deleted after analysis. The collector does not transmit data and every comparison returns `recovery_authorized: false`.

From `agent-core` on each installation, with the same key:

```powershell
$env:PYTHONPATH = 'src'
$studyKey = '<64+ hexadecimal characters generated once with a CSPRNG>'
python -m sarae_agent.activation.device_probe capture --study-key-hex $studyKey --output before.json
# After reinstalling Windows on this same PC:
python -m sarae_agent.activation.device_probe capture --study-key-hex $studyKey --output after.json
python -m sarae_agent.activation.device_probe compare before.json after.json
```

Also capture an unrelated PC using the same study key and compare it with `before.json`. Do **not** enroll these reports in the Cloud. `candidate_same_hardware` requires two matching SMBIOS/board/BIOS fields and no conflicting hardware field. It is an investigation label, never recovery authorization; two fields may derive from one firmware data source and may be cloned.

Current execution-host observation: in the restricted sandbox, the collector read only Machine GUID and four CIM hardware queries raised `CimException`. With read-only host access outside that sandbox, all five signals were available; `Get-Tpm` reported neither present nor ready. No raw identifiers were printed or saved. This is a collection check on one installation, **not** validation of the physical-PC separation hypothesis. Synthetic tests cover OS reinstall (GUID change), SSD change, board replacement, different PC, OEM placeholders, and no TPM; they do not measure false-match rates or resistance to spoofing.

## Identity model to implement after the gate

| Object | Meaning | Lifecycle |
| --- | --- | --- |
| Agent ID | Stable Cloud identity and owner/history foreign key | Never reassign to another owner or another physical PC |
| Device binding | Enrolled hardware evidence and assurance tier, versioned | Set once; recovery may confirm the same device, never silently rebind |
| Activation credential | One active generation for Cloud requests | Rotate atomically on successful recovery; revoke old generation |
| Recovery authorization | Short-lived, single-use, owner-authenticated challenge plus device proof | Never stored as an Agent credential; owner login alone is insufficient |

Target schema: `agent_device_bindings(agent_id PRIMARY KEY, assurance, tpm_ek_ref/attestation metadata, keyed hardware digests, enrolled_at, policy_version)`, `agent_credential_generations(agent_id, generation, credential_hash, status, issued_at, revoked_at)` with a partial unique index for one `ACTIVE` row per Agent, `agent_recovery_challenges` with nonce, expiry, attempt limit, consumed timestamp and owner session reference, and append-only `agent_security_audit`. Maintain `registered_agents.id` and all history foreign keys. Backfill old Agents as `LEGACY_UNBOUND`, not with guessed fingerprints.

## TPM path

At enrollment, create a non-exportable signing key with Microsoft Platform Crypto Provider and challenge-sign a Cloud nonce. Verify *real* TPM attestation, including EK/AIK trust and certificate chain where available, before labeling the key hardware-bound; setting a non-exportable flag or observing `Get-Tpm` alone does not prove this. Store the public key and attested TPM reference, never a private key or raw EK in normal logs. On subsequent requests, require a fresh proof of possession alongside the active credential; a copied bearer then fails on a different TPM. On reinstall, the app key may be gone. Recovery needs a **new attested key proven to belong to the enrolled TPM**, not the lost key. Validate this end-to-end on target Windows/TPM models, including firmware TPM behavior, TPM clear/reset, privacy policy, and EK certificate availability before release. TPM or mainboard replacement may make the original device unprovable and must fail closed.

Microsoft documents TPM key attestation and the endorsement-key trust relationship: <https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/component-updates/tpm-key-attestation>. TPM state query: <https://learn.microsoft.com/en-us/powershell/module/trustedplatformmodule/get-tpm?view=windowsserver2025-ps>.

## No-TPM path

During normal use, protect a locally generated software signing key with current-user DPAPI, send nonce signatures, and bind the Cloud record to keyed SMBIOS/board/BIOS evidence. This stops casual file copying but **cannot prevent an administrator or VM operator from extracting/spoofing software evidence**. After reinstall, both the DPAPI secret and signing key can be lost. Recovery of the old Agent ID must require owner authentication **and** independent, reviewed device evidence (for example, pre-enrolled service/repair records and live physical verification), with explicit audit and a second reviewer for ambiguous cases. If such evidence is unavailable, deny recovery of the old Agent ID as the business rule requires. Do not treat two matching serial strings, owner login, a new activation code, MAC address, or Machine GUID as sufficient. A no-TPM device that cannot meet this assurance level can continue on its current credential while intact; it must be clearly marked as having no guaranteed reinstall recovery.

## Recovery transaction (proposed, not enabled)

1. Owner signs in and selects an existing Agent ID. Cloud checks owner, Agent state, rate limits, and issues a short-lived nonce. No owner or admin can select a new machine for that ID.
2. Candidate Agent supplies the new key/public attestation and current hardware evidence; Cloud evaluates the stored binding and assurance policy. Ambiguous or conflicting evidence yields `DENIED` or manual review, with no credential issued. A still-active old Agent is a conflict signal and should block automated recovery.
3. On approval, use one database transaction with a row lock / `BEGIN IMMEDIATE`: consume the challenge once, revoke generation N, insert generation N+1, update the active pointer, append audit, and commit. The Agent ID, owner and history are untouched. Return the new credential only after commit; client saves it atomically with DPAPI. If the response/save fails, retry with a bounded idempotency key rather than creating a second active generation.
4. Every authenticated Cloud request checks the current generation and device proof. Long polls and in-flight commands must recheck on delivery, so an already-authorized old connection cannot keep issuing work after rotation. Revoke Agent and all credentials atomically, with audit.

Audit event types: activation requested/succeeded/denied, binding enrolled, challenge issued/expired/denied, evidence mismatch, recovery approved/succeeded/failed, credential rotated/revoked, Agent revoked, proof failure. Log Agent ID, owner, generation, policy version, reason codes and timestamps; never log bearer secrets, raw hardware IDs, EK, nonces, or recovery token.

## Misidentification cases and gate

| Scenario | Risk | Treatment |
| --- | --- | --- |
| Windows reinstall | Machine GUID and DPAPI change | Ignore GUID as decisive; test fresh TPM attestation or reviewed no-TPM evidence |
| SSD/RAM replacement | Disk serial changes; RAM is not stable identity | Disk is diagnostic only; RAM excluded |
| Mainboard/TPM replacement | Strong identity changes on same chassis | Fail closed; old ID may be unrecoverable |
| OEM placeholders/duplicate serials | Two PCs can look the same | Reject placeholders; measure cross-device collision rate |
| VM clone or firmware spoof | SMBIOS values copied | TPM attestation where trusted; acknowledge VM/admin spoof limits |
| TPM clear/firmware reset | Key/attestation changes | Manual investigation; no automatic rebinding |
| Legacy installed Agent | No enrolled hardware proof | Keep current ID and credential on update; enroll only while old credential still valid, never claim retroactive proof |

Release gate: run before/after captures on several real PCs across clean Windows reinstall, app reinstall, SSD/RAM changes, and a negative different-PC comparison; record vendor/TPM availability and false matches without raw IDs. Separately prototype Cloud-verified TPM attestation and no-TPM manual evidence review. Until these pass, **do not deploy** binding enforcement or recovery, and do not rotate production credentials.
