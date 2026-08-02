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

function Get-VerificationSignature {
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
    $canonical = "gate=$gate;nodes=$([string]::Join(',', ($nodes | Sort-Object -Unique)));classes=$([string]::Join(',', ($classes | Sort-Object)))"
    $bytes = $utf8NoBom.GetBytes($canonical)
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($hash.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
    }
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
        if ([int]$state.attempts -ge [int]$state.max_attempts) {
            $null = Invoke-Runtime transition $runDirectory failed
            break
        }

        $lesson = $null
        if ($state.last_failure_fingerprint) {
            $lesson = Invoke-Runtime lookup $repo $state.last_failure_fingerprint --action ([string]$state.current_action) --scope repository
        }
        if ($lesson -and $lesson.strategy_id -notin @($state.tried_strategy_ids)) {
            $strategyId = [string]$lesson.strategy_id
            $strategyInstruction = [string]$lesson.instruction
        }
        elseif ($state.status -eq "interrupted" -and "recover_interrupted" -notin @($state.tried_strategy_ids)) {
            $strategyId = "recover_interrupted"
            $strategyInstruction = "Inspect the preserved worktree and run state after interruption, continue from the last safe checkpoint, and verify all resulting work."
        }
        elseif ($state.last_failure_category -eq "verification" -and "repair_verification" -notin @($state.tried_strategy_ids)) {
            $strategyId = "repair_verification"
            $strategyInstruction = "Inspect the failed verification evidence, repair its root cause without discarding valid work, add regression coverage, and verify again."
        }
        elseif ([int]$state.attempts -eq 0) {
            $strategyId = "inspect_fix_verify"
            $strategyInstruction = "Inspect the repository, implement the smallest coherent fix, add regression coverage, and verify it."
        }
        elseif ("transient_retry" -notin @($state.tried_strategy_ids)) {
            $strategyId = "transient_retry"
            $strategyInstruction = "Re-check the runner and current worktree once, preserve completed work, then finish and verify the task without repeating irreversible effects."
        }
        else {
            $null = Invoke-Runtime transition $runDirectory failed
            break
        }

        $null = Invoke-Runtime transition $runDirectory running --owner-pid $PID --increment-attempt --strategy-id $strategyId --action codex
        $state = Get-RunState -Directory $runDirectory
        $attempt = [int]$state.attempts
        $resultPath = Join-Path $runDirectory ("result-attempt-{0}.json" -f $attempt)
        $prompt = @"
Execute this repository task through the development harness in AGENTS.md.
Risk hint: $($state.risk). Classify it yourself if auto; never lower a required tier.
Recovery strategy: $strategyId. $strategyInstruction
Preserve valid existing work. Do not repeat a failed strategy without new evidence.
Run state is in .harness/runs/$runId. The last sanitized failure fingerprint is
$($state.last_failure_fingerprint). Promote it to .harness/memory/lessons.json only
after proving a reusable root cause with regression tests, full verification,
independent review, and commit evidence. Never store executable commands there.
Complete the work, run the required verification, and return the required structured result.
User task:
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
            $null = Invoke-Runtime transition $runDirectory retry_wait
            $failure = Invoke-Runtime failure $runDirectory external_runner "Codex runner exited before completing the task" --action codex --code ([string]$codexExit)
            $state = Get-RunState -Directory $runDirectory
            if ([int]$state.failure_counts.($failure.fingerprint) -ge 2 -or [int]$state.attempts -ge [int]$state.max_attempts) {
                $null = Invoke-Runtime transition $runDirectory failed
                break
            }
            continue
        }

        $null = Invoke-Runtime transition $runDirectory verifying --owner-pid $PID --action verify --complete-action codex
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
            $null = Invoke-Runtime transition $runDirectory completed --complete-action verify
            Copy-Item -LiteralPath $resultPath -Destination (Join-Path $runDirectory "result.json") -Force
            Remove-Item -LiteralPath $taskPath -Force
            break
        }

        $null = Invoke-Runtime transition $runDirectory retry_wait
        $verifySignature = Get-VerificationSignature -Lines $verifyOutput
        $failure = Invoke-Runtime failure $runDirectory verification "The repository verification gate failed signature=$verifySignature" --action verify --code ([string]$verifyExit)
        $state = Get-RunState -Directory $runDirectory
        if ([int]$state.failure_counts.($failure.fingerprint) -ge 2 -or [int]$state.attempts -ge [int]$state.max_attempts) {
            $null = Invoke-Runtime transition $runDirectory failed
            break
        }
    }

    $finalState = Get-RunState -Directory $runDirectory
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
