param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Task,

    [ValidateSet("auto", "R0", "R1", "R2", "R3")]
    [string]$Risk = "auto"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$codex = Get-Command codex.exe -ErrorAction Stop
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runDirectory = Join-Path $repo ".harness\runs\$timestamp"
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$resultPath = Join-Path $runDirectory "result.json"

$prompt = @"
Execute this repository task through the development harness in AGENTS.md.
Risk hint: $Risk. Classify it yourself if auto; never lower a required tier.
Use native subagents only when the risk policy requires them. Complete the work,
verify it, update durable memory only when justified, and return the required
structured result. Task: $Task
"@

& $codex.Source `
    --ask-for-approval never `
    --sandbox workspace-write `
    --search `
    --cd $repo `
    exec `
    --strict-config `
    --ephemeral `
    --output-schema (Join-Path $repo ".codex\schemas\result.schema.json") `
    --output-last-message $resultPath `
    $prompt

if ($LASTEXITCODE -ne 0) {
    throw "Harness run failed with exit code $LASTEXITCODE. See $runDirectory"
}

Write-Host "Harness completed: $resultPath"
