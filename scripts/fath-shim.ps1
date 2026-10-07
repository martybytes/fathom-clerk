# fath-shim.ps1 -- the `fath` command.
#
# Dot-source this from $PROFILE, or let scripts/install-shim.ps1 splice it in.
# It lives in the fathom-helper clone rather than being pasted into the profile
# so that a fix ships with `git pull` instead of a profile re-sync.
#
# Resolution: the clone is found from this script's own location, so moving the
# clone moves the command with it and there is no path to keep in sync.

# The clone this file sits in. $PSScriptRoot is scripts/, so the parent is root.
$script:FathRoot = Split-Path -Parent $PSScriptRoot

function Get-FathPython {
    <#
    .SYNOPSIS
    Python 3.10 or newer, or $null.

    `py -3` is tried first on purpose: a bare `python` on a fresh Windows box is
    often the Microsoft Store stub, which opens a store page instead of running
    anything.
    #>
    foreach ($candidate in @(
            @{ Exe = 'py';      Arguments = @('-3') },
            @{ Exe = 'python';  Arguments = @() },
            @{ Exe = 'python3'; Arguments = @() })) {
        $found = Get-Command $candidate.Exe -CommandType Application `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $found) { continue }
        try {
            $reported = & $found.Source @($candidate.Arguments + @(
                    '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'))
            if ($reported -and ([version]$reported.Trim() -ge [version]'3.10')) {
                return [pscustomobject]@{ Exe = $found.Source; Arguments = $candidate.Arguments }
            }
        } catch {}
    }
    return $null
}

function Get-FathCommandNames {
    param([string]$SourceDir)
    $table = Join-Path $SourceDir 'fath\commands.conf'
    if (-not (Test-Path -LiteralPath $table)) { return @() }
    Get-Content -LiteralPath $table |
        Where-Object { $_ -match '^\s*[^#\s]' } |
        ForEach-Object { (-split $_)[0] } |
        Where-Object { $_ }
}

# No param block on purpose. A [CmdletBinding()] parameter set would try to bind
# `--version` and `-h` as PowerShell parameter names before fath ever saw them.
function Invoke-Fath {
    $python = Get-FathPython
    if (-not $python) {
        Write-Warning 'fath: Python 3.10+ not found; it is required. Install it, then rerun.'
        return
    }

    $entry = Join-Path $script:FathRoot 'fath\main.py'
    if (-not (Test-Path -LiteralPath $entry)) {
        Write-Warning "fath: cannot find $entry -- has the clone moved?"
        return
    }

    # The @() is load-bearing. An `if` used as an expression unrolls a
    # single-element result to a scalar, and splatting a scalar string that
    # starts with '-' re-parses it as a parameter token: `fath sync -h` would
    # arrive as two arguments, '-' and 'h'. With no tail it splats one empty
    # string. Both parse cleanly and both are silent.
    $forwarded = @(if ($args.Count -gt 0) { $args } else { @() })

    # Set for the child only, then restored. Leaving it set would turn a one-shot
    # resolution into a session-wide pin.
    $previous = $env:FATHOM_HELPER_DIR
    try {
        $env:FATHOM_HELPER_DIR = $script:FathRoot
        & $python.Exe @($python.Arguments + @($entry) + $forwarded)
    } finally {
        if ($null -eq $previous) {
            Remove-Item Env:\FATHOM_HELPER_DIR -ErrorAction SilentlyContinue
        } else {
            $env:FATHOM_HELPER_DIR = $previous
        }
    }
}

Set-Alias -Name fath -Value Invoke-Fath -Scope Global -Force

# Completion reads the same table the dispatcher does, cached per session so TAB
# never pays for a file read.
$script:FathSubcommands = @()
Register-ArgumentCompleter -Native -CommandName fath -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    if (-not $script:FathSubcommands -or $script:FathSubcommands.Count -eq 0) {
        $script:FathSubcommands = Get-FathCommandNames -SourceDir $script:FathRoot
    }
    $script:FathSubcommands |
        Where-Object { $_ -like "$wordToComplete*" } |
        ForEach-Object {
            [System.Management.Automation.CompletionResult]::new(
                $_, $_, 'ParameterValue', $_)
        }
}
