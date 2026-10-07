# install-shim.ps1 -- make `fath` a command in every new PowerShell session.
#
# Splices a marker block into a profile that dot-sources scripts/fath-shim.ps1
# from this clone. Spliced, never whole-file written: the profile is a file its
# owner also edits.
#
# Which profile:
#   By default, $PROFILE.
#   -ProfilePath targets any other file.

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [string]$ProfilePath,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'

$startMarker = '# ---- fathom-helper-start ----'
$endMarker   = '# ---- fathom-helper-end ----'

$cloneRoot = Split-Path -Parent $PSScriptRoot
$shimPath  = Join-Path $cloneRoot 'scripts\fath-shim.ps1'

if (-not (Test-Path -LiteralPath $shimPath)) {
    throw "cannot find $shimPath"
}

function Resolve-TargetProfile {
    if ($ProfilePath) { return $ProfilePath }
    return $PROFILE
}

$target = Resolve-TargetProfile
Write-Host "profile: $target"

$existing = if (Test-Path -LiteralPath $target) {
    Get-Content -LiteralPath $target -Raw
} else {
    ''
}

# Collect into an array and pass -Value. Piping Where-Object straight into
# Set-Content writes nothing at all when the pipeline is empty -- silently, with
# no error -- which is how a profile gets truncated instead of edited.
$lines = @($existing -split "`r?`n")
$kept = [System.Collections.Generic.List[string]]::new()
$inBlock = $false
$hadBlock = $false
foreach ($line in $lines) {
    if ($line.Trim() -eq $startMarker) { $inBlock = $true; $hadBlock = $true; continue }
    if ($line.Trim() -eq $endMarker)   { $inBlock = $false; continue }
    if (-not $inBlock) { $kept.Add($line) }
}

if ($Uninstall) {
    if (-not $hadBlock) {
        Write-Host 'nothing to remove.'
        return
    }
    $updated = ($kept -join "`r`n").TrimEnd() + "`r`n"
} else {
    $block = @(
        $startMarker
        '# The `fath` command. The body lives in the fathom-helper clone so a fix'
        '# ships with `git pull` rather than a profile re-sync.'
        "`$fathShim = '$shimPath'"
        'if (Test-Path -LiteralPath $fathShim) { . $fathShim }'
        $endMarker
    )
    $body = ($kept -join "`r`n").TrimEnd()
    $updated = if ($body) {
        $body + "`r`n`r`n" + ($block -join "`r`n") + "`r`n"
    } else {
        ($block -join "`r`n") + "`r`n"
    }
}

if ($WhatIfOnly) {
    Write-Host '--- would write ---'
    Write-Host $updated
    return
}

# Dated backup, never clobbering a same-day one.
if (Test-Path -LiteralPath $target) {
    $stamp = Get-Date -Format 'yyyyMMdd'
    $backup = "$target.bak.$stamp"
    $n = 1
    while (Test-Path -LiteralPath $backup) {
        $backup = "$target.bak.$stamp.$n"
        $n++
    }
    Copy-Item -LiteralPath $target -Destination $backup
    Write-Host "backup : $backup"
}

$parent = Split-Path -Parent $target
if ($parent -and -not (Test-Path -LiteralPath $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
}
Set-Content -LiteralPath $target -Value $updated -Encoding UTF8 -NoNewline

if ($Uninstall) {
    Write-Host 'removed. Open a new PowerShell session.' -ForegroundColor Green
} else {
    Write-Host 'installed. Run `. $PROFILE` (or open a new session), then: fath doctor' -ForegroundColor Green
}
