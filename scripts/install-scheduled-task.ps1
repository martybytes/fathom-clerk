# install-scheduled-task.ps1 -- run `fath sync` on a schedule.
#
# A Scheduled Task, not an HKCU Run key. That choice is recorded in
# docs/decisions.md: the Run key fires once at logon, and this needs to
# repeat through the day.
#
# Notable settings and why:
#   -StartWhenAvailable        a laptop that was asleep at 08:00 still catches up
#   -DontStopIfGoingOnBatteries a sync is seconds of network, not a build
#   -MultipleInstances IgnoreNew  a slow backfill must not stack up behind itself
#   -RunLevel Limited          nothing here needs elevation, and a task that
#                              demands it cannot run unattended without a prompt
#   no stored password         the key lives in .env; asking for a password to
#                              read it would be a worse trade

[CmdletBinding()]
param(
    [int]$IntervalHours = 4,
    [string]$StartTime = '07:00',
    [string]$TaskName = 'fathom-helper-sync',
    [string]$TaskPath = '\martybytes\',
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$RunNow
)

$ErrorActionPreference = 'Stop'

$cloneRoot = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $cloneRoot 'fath\main.py'

function Get-Task {
    Get-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -ErrorAction SilentlyContinue
}

function Show-Status {
    $task = Get-Task
    if (-not $task) {
        Write-Host 'not installed'
        return
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -TaskPath $TaskPath
    Write-Host "state        : $($task.State)"
    Write-Host "last run     : $($info.LastRunTime)"
    # 0 is success; 267009 means 'currently running'. Anything else is a real
    # failure and the reason will be in fath.log.
    Write-Host "last result  : $($info.LastTaskResult)"
    Write-Host "next run     : $($info.NextRunTime)"
    $action = $task.Actions | Select-Object -First 1
    Write-Host "runs         : $($action.Execute) $($action.Arguments)"
}

if ($Status) { Show-Status; return }

if ($Uninstall) {
    if (Get-Task) {
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
        Write-Host "removed $TaskPath$TaskName" -ForegroundColor Green
    } else {
        Write-Host 'nothing to remove.'
    }
    return
}

if ($RunNow) {
    if (-not (Get-Task)) { throw "task $TaskPath$TaskName is not installed" }
    Start-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath
    Write-Host 'started. Check progress with -Status, or in the log.'
    return
}

# -- find a windowless Python ---------------------------------------------- #
# pythonw.exe so an unattended run never flashes a console window. Falling back
# to python.exe is better than not running at all.
$pythonw = $null
foreach ($candidate in @('pythonw.exe', 'python.exe')) {
    $found = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { $pythonw = $found.Source; break }
}
if (-not $pythonw) { throw 'no python found on PATH' }

if (-not (Test-Path -LiteralPath $entry)) { throw "cannot find $entry" }

# -- bake in the settings root when it is not the default ------------------- #
# The task inherits the *logon* environment, so $FATH_HOME set in a shell never
# reaches it. Reading the pointer file would work, but putting the resolved path
# in the action arguments makes the resolution visible in the task definition --
# which is where anyone debugging an unattended run will actually look.
$homeArgs = @()
$pointer = Join-Path $env:LOCALAPPDATA 'fathom-helper\home.path'
if (Test-Path -LiteralPath $pointer) {
    $resolved = (Get-Content -LiteralPath $pointer -Raw).Trim()
    if ($resolved) { $homeArgs = @('--home', "`"$resolved`"") }
}

$arguments = (@("`"$entry`"") + $homeArgs + @('sync', '--quiet')) -join ' '

$action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $cloneRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $StartTime
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $StartTime `
        -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
        -RepetitionDuration (New-TimeSpan -Days 1)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 15)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath $TaskPath `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Fetch new Fathom meetings into folders. Every $IntervalHours hours." `
    -Force | Out-Null

Write-Host "installed $TaskPath$TaskName" -ForegroundColor Green
Write-Host "  runs    : $pythonw $arguments"
Write-Host "  every   : $IntervalHours hours from $StartTime"
Write-Host "  log     : see `fath status`, or the log under the settings root"
Write-Host ''
Show-Status
