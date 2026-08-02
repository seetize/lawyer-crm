param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$watchdogTaskName = "BeautyInspector-HarnessWatchdog"
$recoveryTaskName = "BeautyInspector-HarnessRecovery"
if ($Uninstall) {
    foreach ($taskName in @($watchdogTaskName, $recoveryTaskName)) {
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    Write-Host "Watchdog and recovery tasks are not installed."
    exit 0
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$watchdog = Join-Path $repo "scripts\watchdog.ps1"
$recovery = Join-Path $repo "scripts\recover_harness.ps1"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$watchdogArguments = "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$watchdog`" -Once"
$recoveryArguments = "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$recovery`""
$watchdogAction = New-ScheduledTaskAction -Execute $powershell -Argument $watchdogArguments -WorkingDirectory $repo
$recoveryAction = New-ScheduledTaskAction -Execute $powershell -Argument $recoveryArguments -WorkingDirectory $repo
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$watchdogTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$recoveryTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$watchdogSettings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$recoverySettings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $watchdogTaskName `
    -Action $watchdogAction `
    -Trigger @($logonTrigger, $watchdogTrigger) `
    -Principal $principal `
    -Settings $watchdogSettings `
    -Description "Restarts the exact Beauty Inspector bot process with a bounded circuit breaker." `
    -Force | Out-Null
Register-ScheduledTask `
    -TaskName $recoveryTaskName `
    -Action $recoveryAction `
    -Trigger @($logonTrigger, $recoveryTrigger) `
    -Principal $principal `
    -Settings $recoverySettings `
    -Description "Resumes interrupted Beauty Inspector harness runs without a scheduler time limit." `
    -Force | Out-Null
Write-Host "Watchdog and recovery tasks installed for the current Windows user."
