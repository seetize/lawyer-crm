param(
    [Parameter(Mandatory = $true, ParameterSetName = "New")]
    [ValidateNotNullOrEmpty()]
    [string]$Task,

    [Parameter(Mandatory = $true, ParameterSetName = "Resume")]
    [ValidatePattern("^[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$")]
    [string]$ResumeRun,

    [ValidateSet("auto", "R0", "R1", "R2", "R3")]
    [string]$Risk = "auto",

    [ValidateRange(1, 3)]
    [int]$MaxAttempts = 2
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$runsRoot = Join-Path $repo ".harness\runs"
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}
$codex = Get-Command codex.exe -ErrorAction Stop
$runtime = Join-Path $repo "scripts\harness_runtime.py"
$schema = Join-Path $repo ".codex\schemas\result.schema.json"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$mutex = [System.Threading.Mutex]::new($false, "Local\BeautyInspectorHarnessWriter")
$hasMutex = $false

function Invoke-Runtime {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & $python -B $runtime @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Harness state operation failed"
    }
    return ($output | ConvertFrom-Json)
}

function Get-RunState {
    param([string]$Directory)
    return (Invoke-Runtime show $Directory)
}

function Get-VerificationEvidence {
    param([object[]]$Lines)
    $gate = "unknown"
    $nodes = [System.Collections.Generic.List[string]]::new()
    $classes = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($item in $Lines) {
        $line = ([string]$item).Trim()
        if ($line -match "^\[gate\]\s+(.+)$") {
            $gate = $Matches[1].Trim().ToLowerInvariant()
        }
        if ($line -match "^FAILED\s+([^\s]+)" -and $nodes.Count -lt 10) {
            $nodes.Add(($Matches[1] -replace "\\", "/").ToLowerInvariant())
        }
        foreach ($match in [regex]::Matches($line, "\b[A-Za-z][A-Za-z0-9_]*(?:Error|Exception)\b")) {
            $null = $classes.Add($match.Value.ToLowerInvariant())
        }
    }
    return "gate=$gate;nodes=$([string]::Join(',', ($nodes | Sort-Object -Unique)));classes=$([string]::Join(',', ($classes | Sort-Object)))"
}

function Register-RunFailure {
    param(
        [string]$Directory,
        [string]$Category,
        [string]$Message,
        [string]$Action,
        [string]$Code
    )
    $null = Invoke-Runtime transition $Directory retry_wait
    $failure = Invoke-Runtime failure $Directory $Category $Message --action $Action --code $Code
    $state = Get-RunState -Directory $Directory
    $stop = (
        [int]$state.failure_counts.($failure.fingerprint) -ge 2 -or
        [int]$state.attempts -ge [int]$state.max_attempts
    )
    if ($stop) {
        $null = Invoke-Runtime transition $Directory failed
    }
    return (-not $stop)
}

function Protect-Task {
    param([string]$PlainText, [string]$Path)
    $secure = ConvertTo-SecureString $PlainText -AsPlainText -Force
    $protected = ConvertFrom-SecureString $secure
    [System.IO.File]::WriteAllText($Path, $protected, $utf8NoBom)
}

function Unprotect-Task {
    param([string]$Path)
    $protected = [System.IO.File]::ReadAllText($Path, $utf8NoBom)
    $secure = ConvertTo-SecureString $protected
    return [System.Net.NetworkCredential]::new("", $secure).Password
}

try {
    try {
        $hasMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $hasMutex = $true
    }
    if (-not $hasMutex) {
        throw "Another harness writer is active; this run was not started"
    }

    New-Item -ItemType Directory -Path $runsRoot -Force | Out-Null
    if ($PSCmdlet.ParameterSetName -eq "New") {
        $runId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
        $runDirectory = Join-Path $runsRoot $runId
        New-Item -ItemType Directory -Path $runDirectory | Out-Null
        $taskPath = Join-Path $runDirectory "task.dpapi"
        Protect-Task -PlainText $Task -Path $taskPath
        $null = Invoke-Runtime init $runDirectory --risk $Risk --max-attempts $MaxAttempts
    }
    else {
        $runId = $ResumeRun
        $runDirectory = [System.IO.Path]::GetFullPath((Join-Path $runsRoot $runId))
        $safeRoot = [System.IO.Path]::GetFullPath($runsRoot) + [System.IO.Path]::DirectorySeparatorChar
        if (-not $runDirectory.StartsWith($safeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Invalid run path"
        }
        $taskPath = Join-Path $runDirectory "task.dpapi"
        if (-not (Test-Path -LiteralPath $taskPath -PathType Leaf)) {
            throw "The recoverable task payload is missing"
        }
        $Task = Unprotect-Task -Path $taskPath
        $resumeState = Get-RunState -Directory $runDirectory
        if ($resumeState.status -in @("running", "verifying")) {
            $null = Invoke-Runtime transition $runDirectory interrupted
        }
    }

    while ($true) {
        $state = Get-RunState -Directory $runDirectory
        if ($state.status -in @("completed", "failed")) {
            break
        }
        $attempt = [int]$state.attempts
        $resultPath = Join-Path $runDirectory ("result-attempt-{0}.json" -f $attempt)
        $resumeVerification = (
            $state.status -eq "interrupted" -and
            "codex" -in @($state.completed_actions) -and
            (Test-Path -LiteralPath $resultPath -PathType Leaf)
        )
        if ($resumeVerification) {
            try {
                $resumeResult = Invoke-Runtime result $resultPath
                $resumeVerification = $resumeResult.status -eq "complete"
            }
            catch {
                $resumeVerification = $false
            }
        }
        if (-not $resumeVerification -and [int]$state.attempts -ge [int]$state.max_attempts) {
            $null = Invoke-Runtime transition $runDirectory failed
            break
        }

        if (-not $resumeVerification) {
            $strategy = Invoke-Runtime strategy $repo $runDirectory
            if (-not $strategy) {
                $null = Invoke-Runtime transition $runDirectory failed
                break
            }
            $strategyId = [string]$strategy.strategy_id
            $strategyInstruction = [string]$strategy.instruction

            $null = Invoke-Runtime transition $runDirectory running --owner-pid $PID --increment-attempt --strategy-id $strategyId --action codex
            $state = Get-RunState -Directory $runDirectory
            $attempt = [int]$state.attempts
            $resultPath = Join-Path $runDirectory ("result-attempt-{0}.json" -f $attempt)
            $prompt = @"
Follow AGENTS.md. Risk: $($state.risk). Strategy: $strategyId — $strategyInstruction
On recovery inspect .harness/runs/$runId/failures.json. Preserve valid work.
Run targeted checks only; this wrapper runs the full gate once. Return structured evidence.
Task:
$Task
"@

            $OutputEncoding = $utf8NoBom
            $env:PYTHONIOENCODING = "utf-8"
            $prompt | & $codex.Source `
                --ask-for-approval never `
                --sandbox workspace-write `
                --search `
                --cd $repo `
                exec `
                --strict-config `
                --ephemeral `
                --output-schema $schema `
                --output-last-message $resultPath `
                -
            $codexExit = $LASTEXITCODE

            if ($codexExit -ne 0) {
                if (-not (Register-RunFailure $runDirectory external_runner "Codex runner exited before completing the task" codex ([string]$codexExit))) {
                    break
                }
                continue
            }

            try {
                $agentResult = Invoke-Runtime result $resultPath
            }
            catch {
                if (-not (Register-RunFailure $runDirectory result_contract "Codex returned an invalid structured result" codex invalid)) {
                    break
                }
                continue
            }
            if ($agentResult.status -ne "complete") {
                $summary = ([string]$agentResult.summary).Replace("`r", " ").Replace("`n", " ")
                if (-not (Register-RunFailure $runDirectory agent_incomplete "Codex reported $($agentResult.status): $summary" codex ([string]$agentResult.status))) {
                    break
                }
                continue
            }
            $null = Invoke-Runtime transition $runDirectory verifying --owner-pid $PID --action verify --complete-action codex
        }
        else {
            $null = Invoke-Runtime transition $runDirectory verifying --owner-pid $PID --action verify
        }

        $verifyExit = 0
        $verifyOutput = @()
        try {
            $verifyOutput = @(& (Join-Path $repo "scripts\verify.ps1") *>&1)
            $verifyExit = $LASTEXITCODE
        }
        catch {
            $verifyOutput += $_
            $verifyExit = 1
        }
        if ($verifyExit -eq 0) {
            $canonicalResult = Join-Path $runDirectory "result.json"
            Copy-Item -LiteralPath $resultPath -Destination $canonicalResult -Force
            $null = Invoke-Runtime result $canonicalResult
            $null = Invoke-Runtime transition $runDirectory completed --complete-action verify
            Remove-Item -LiteralPath $taskPath -Force
            break
        }

        $verifyEvidence = Get-VerificationEvidence -Lines $verifyOutput
        if (-not (Register-RunFailure $runDirectory verification "Repository verification failed: $verifyEvidence" verify ([string]$verifyExit))) {
            break
        }
    }

    $finalState = Get-RunState -Directory $runDirectory
    if ($finalState.status -eq "failed" -and (Test-Path -LiteralPath $taskPath -PathType Leaf)) {
        Remove-Item -LiteralPath $taskPath -Force
    }
    Write-Host "Harness run $runId finished with status $($finalState.status)."
    if ($finalState.status -ne "completed") {
        exit 1
    }
}
finally {
    $Task = $null
    if ($hasMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
