param(
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$policy = Join-Path $root 'data\policies\te003-live-gpt-4-1-mini.2026-08-31.1.json'

$env:TOKENECONOMICS_PORT = [string]$Port
$env:TOKENGOV_POLICY_SOURCE = 'file'
$env:TOKENGOV_POLICY_FILE = $policy

Push-Location $root
try {
    python studio.py
}
finally {
    Pop-Location
}
