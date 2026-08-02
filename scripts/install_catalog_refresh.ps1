param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$taskName = "BeautyInspector-CatalogRefresh"
if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Write-Host "Catalog refresh task is not installed."
    exit 0
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$runner = Join-Path $repo "scripts\catalog_refresh.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python interpreter is missing"
}
$arguments = "-B `"$runner`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $repo
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$periodicTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(3) `
    -RepetitionInterval (New-TimeSpan -Hours 6) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($logonTrigger, $periodicTrigger) `
    -Principal $principal `
    -Settings $settings `
    -Description "Refreshes the SINDY city catalogue idempotently and enriches a bounded batch." `
    -Force | Out-Null
Write-Host "Catalog refresh task installed for the current Windows user."

