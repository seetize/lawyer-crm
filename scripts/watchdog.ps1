param([switch]$Once)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
$runner = Join-Path $repo "scripts\run_bot.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python interpreter is missing"
}
$statePath = Join-Path $repo ".harness\runs\watchdog-state.json"
$stdoutPath = Join-Path $repo "bot.stdout.log"
$stderrPath = Join-Path $repo "bot.stderr.log"
$heartbeatPath = Join-Path $repo ".harness\runs\bot-heartbeat.json"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$mutex = [System.Threading.Mutex]::new($false, "Local\BeautyInspectorBotWatchdog")
$hasMutex = $false

function Save-WatchdogState {
    param([object]$State)
    $directory = Split-Path -Parent $statePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = Join-Path $directory (".watchdog-{0}.tmp" -f ([guid]::NewGuid().ToString("N")))
    [System.IO.File]::WriteAllText($temporary, ($State | ConvertTo-Json -Depth 4), $utf8NoBom)
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}

function Load-WatchdogState {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return [pscustomobject]@{ restarts = @(); circuit_open_until = $null; last_status = "new" }
    }
    try {
        return (Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json)
    }
    catch {
        return [pscustomobject]@{
            restarts = @()
            circuit_open_until = (Get-Date).ToUniversalTime().AddHours(1).ToString("o")
            last_status = "state_corrupt_fail_closed"
        }
    }
}

function Get-BotProcess {
    $escapedRunner = [regex]::Escape($runner)
    return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
        $_.ExecutablePath -and
        $_.ExecutablePath -ieq $python -and
        $_.CommandLine -match $escapedRunner
    })
}

function Test-BotHeartbeat {
    if (-not (Test-Path -LiteralPath $heartbeatPath -PathType Leaf)) {
        return $false
    }
    try {
        $heartbeat = Get-Content -Raw -LiteralPath $heartbeatPath | ConvertFrom-Json
        $updated = [datetime]::Parse([string]$heartbeat.updated_at).ToUniversalTime()
        return $updated -gt (Get-Date).ToUniversalTime().AddMinutes(-2)
    }
    catch {
        return $false
    }
}

function Stop-BotProcessTree {
    param([object[]]$Processes)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($process in $Processes) {
        $null = $ids.Add([int]$process.ProcessId)
    }
    $allPython = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'")
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $allPython) {
            if ($ids.Contains([int]$process.ParentProcessId) -and $ids.Add([int]$process.ProcessId)) {
                $changed = $true
            }
        }
    }
    foreach ($processId in @($ids | Sort-Object -Descending)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
}

function Rotate-Log {
    param([string]$Path)
    for ($index = 3; $index -ge 2; $index--) {
        $source = "$Path.$($index - 1)"
        $destination = "$Path.$index"
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Move-Item -LiteralPath $source -Destination $destination -Force
        }
    }
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
    }
}

try {
    try {
        $hasMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (-not $hasMutex) {
        exit 0
    }

    $state = Load-WatchdogState
    $now = (Get-Date).ToUniversalTime()
    $recent = @($state.restarts | Where-Object {
        try { [datetime]::Parse([string]$_).ToUniversalTime() -gt $now.AddHours(-1) } catch { $false }
    })
    $circuitOpenUntil = $null
    if ($state.circuit_open_until) {
        try { $circuitOpenUntil = [datetime]::Parse([string]$state.circuit_open_until).ToUniversalTime() } catch { $circuitOpenUntil = $now.AddHours(1) }
    }

    $processes = @(Get-BotProcess)
    if ($processes.Count -gt 0 -and (Test-BotHeartbeat)) {
        Save-WatchdogState ([pscustomobject]@{
            restarts = $recent
            circuit_open_until = $null
            last_status = "healthy_process_present"
            checked_at = $now.ToString("o")
        })
        exit 0
    }
    if ($processes.Count -gt 0) {
        Stop-BotProcessTree -Processes $processes
    }

    if (($circuitOpenUntil -and $circuitOpenUntil -gt $now) -or $recent.Count -ge 3) {
        $until = if ($circuitOpenUntil -and $circuitOpenUntil -gt $now) { $circuitOpenUntil } else { $now.AddHours(1) }
        Save-WatchdogState ([pscustomobject]@{
            restarts = $recent
            circuit_open_until = $until.ToString("o")
            last_status = "restart_circuit_open"
            checked_at = $now.ToString("o")
        })
        exit 2
    }

    Rotate-Log -Path $stdoutPath
    Rotate-Log -Path $stderrPath
    Start-Process -FilePath $python `
        -ArgumentList @("-B", $runner) `
        -WorkingDirectory $repo `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath | Out-Null
    $recent += $now.ToString("o")
    Save-WatchdogState ([pscustomobject]@{
        restarts = $recent
        circuit_open_until = $null
        last_status = "bot_restarted"
        checked_at = $now.ToString("o")
    })
}
finally {
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
