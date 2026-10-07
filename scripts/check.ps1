# check.ps1 -- every gate, in one command. Run this before committing anything.
#
# One entry point rather than a list in a document, because a list in a document
# is a list somebody runs half of. Missing tooling is reported, never silently
# skipped: a gate you believe is running and is not is worse than no gate.
#
#   .\scripts\check.ps1              everything
#   .\scripts\check.ps1 -Quick       skip the slow end-to-end suite
#   .\scripts\check.ps1 -Live        also run the tests that need a real API key
#   .\scripts\check.ps1 -Fix         apply ruff's autofixes first

[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$Live,
    [switch]$Fix
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root

$results = [System.Collections.Generic.List[object]]::new()

function Invoke-Gate {
    param([string]$Name, [scriptblock]$Body, [switch]$Optional)
    Write-Host ""
    Write-Host "-> $Name" -ForegroundColor Cyan
    $started = Get-Date
    & $Body 2>&1 | ForEach-Object { Write-Host "   $_" }
    $code = $LASTEXITCODE
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)

    $status = if ($code -eq 0) { 'pass' } elseif ($Optional) { 'skip' } else { 'FAIL' }
    $results.Add([pscustomobject]@{ Gate = $Name; Status = $status; Seconds = $seconds })
}

function Test-Tool {
    param([string]$Name)
    $found = $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
    if (-not $found) {
        Write-Host "   $Name is not installed -- this gate did NOT run" -ForegroundColor Yellow
    }
    return $found
}

if ($Fix) {
    Write-Host "applying autofixes..." -ForegroundColor DarkGray
    python -m ruff check fath tests --fix | Out-Null
    python -m ruff format fath tests | Out-Null
}

Invoke-Gate 'ruff (lint)'    { python -m ruff check fath tests }
Invoke-Gate 'ruff (format)'  { python -m ruff format --check fath tests }
Invoke-Gate 'mypy'           { python -m mypy }

$pytestArgs = @('-q')
if (-not $Live) { $pytestArgs += @('-m', 'not live') }
if ($Quick) { $pytestArgs += @('-m', 'not slow and not live') }

Invoke-Gate 'pytest'         { python -m pytest @pytestArgs --cov }

if (Test-Path (Join-Path $root 'web\node_modules')) {
    Invoke-Gate 'tsc'        { Push-Location web; npm run typecheck; Pop-Location }

    # A committed bundle that is older than its source is worse than a missing
    # one: `fath web` serves it happily and shows yesterday's UI.
    Invoke-Gate 'web bundle is current' {
        $newestSource = Get-ChildItem web\src, web\index.html, web\vite.config.ts -Recurse -File |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $bundle = Join-Path $root 'web\dist\assets\app.js'
        if (-not (Test-Path $bundle)) {
            Write-Host 'web/dist is missing -- run: cd web; npm run build'
            $global:LASTEXITCODE = 1
        } elseif ($newestSource.LastWriteTime -gt (Get-Item $bundle).LastWriteTime) {
            Write-Host "stale: $($newestSource.Name) is newer than the bundle"
            Write-Host 'rebuild with: cd web; npm run build'
            $global:LASTEXITCODE = 1
        } else {
            Write-Host 'bundle is newer than its sources'
            $global:LASTEXITCODE = 0
        }
    }
} else {
    Write-Host ''
    Write-Host '   web/node_modules missing -- the UI gates did NOT run' -ForegroundColor Yellow
    Write-Host '   install with: cd web; npm install' -ForegroundColor Yellow
    $results.Add([pscustomobject]@{ Gate = 'tsc'; Status = 'skip'; Seconds = 0 })
}

Pop-Location

Write-Host ''
Write-Host ('=' * 52)
$results | Format-Table -AutoSize | Out-String | Write-Host

$failed = @($results | Where-Object Status -eq 'FAIL')
$skipped = @($results | Where-Object Status -eq 'skip')

if ($skipped.Count) {
    Write-Host "$($skipped.Count) gate(s) did not run." -ForegroundColor Yellow
}
if ($failed.Count) {
    Write-Host "$($failed.Count) gate(s) failed: $($failed.Gate -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'all gates passed' -ForegroundColor Green
exit 0
