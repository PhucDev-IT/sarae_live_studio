#Requires -Version 5.1
# Starts the Sarae Voice Server in the foreground (one process, one GPU worker). Ctrl+C stops it.
# Creates voice-server\.env with a random orchestrator token the first time.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Run scripts\install.ps1 first" }

if (-not (Test-Path .env)) {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    (Get-Content .env.example) -replace '^SARAE_VOICE_ORCHESTRATOR_TOKENS=$', "SARAE_VOICE_ORCHESTRATOR_TOKENS=$token" |
        Set-Content -Encoding utf8 .env
    Write-Host "Created .env with a new orchestrator token (see SARAE_VOICE_ORCHESTRATOR_TOKENS in .env)." -ForegroundColor Yellow
}
$env:PYTHONIOENCODING = "utf-8"
& $py -m sarae_voice
