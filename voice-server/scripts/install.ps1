#Requires -Version 5.1
# Creates voice-server\.venv (Python 3.12) with PyTorch 2.8.0 + CUDA 12.8 and the pinned OmniVoice stack.
# Independent of backend\ and agent-core\: nothing here is imported by the other Sarae components.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv not found. Install uv (https://docs.astral.sh/uv/) and open a new PowerShell."
}
uv python install 3.12
if (-not (Test-Path .venv\Scripts\python.exe)) {
    uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { throw "Creating .venv failed" }
}
$py = Join-Path $root ".venv\Scripts\python.exe"
uv pip install --python $py torch==2.8.0 torchaudio==2.8.0 --index https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw "Installing PyTorch CUDA failed" }
uv pip install --python $py -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Installing requirements failed" }
uv pip install --python $py -e ".[dev,bench]"
if ($LASTEXITCODE -ne 0) { throw "Installing the sarae_voice package failed" }
& $py -c "import torch, omnivoice; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), 'omnivoice', omnivoice.__version__ if hasattr(omnivoice, '__version__') else '?')"
