param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$runsRoot = Join-Path $repo ".harness\runs"
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = (Get-Command python.exe -ErrorAction Stop).Source
}

$json = & $python -B (Join-Path $repo "scripts\harness_runtime.py") recoverable $runsRoot
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect recoverable harness runs"
}
$parsed = $json | ConvertFrom-Json
$runIds = @($parsed | Where-Object { $_ })
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
foreach ($runId in $runIds) {
    & $powershell `
        -NoProfile `
        -NonInteractive `
        -ExecutionPolicy RemoteSigned `
        -File (Join-Path $repo "scripts\harness.ps1") `
        -ResumeRun ([string]$runId)
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Harness run $runId remains incomplete; recovery will not loop in this invocation."
    }
}
