param(
    [switch]$Live
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    $python = $pythonCommand.Source
}
Set-Location -LiteralPath $repo
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Gate {
    param([string]$Name, [scriptblock]$Action)
    Write-Host "[gate] $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Invoke-Gate "diff integrity" { git diff HEAD --check }

Write-Host "[gate] tracked and untracked secret patterns"
$secretPattern = "(sk-(proj-)?[A-Za-z0-9_-]{20,}|[0-9]{8,12}:AA[A-Za-z0-9_-]{25,})"
$candidateFiles = @(git ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) {
    throw "Could not enumerate files for secret scan"
}
$secretFiles = @()
foreach ($relativePath in $candidateFiles) {
    $path = Join-Path $repo $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        continue
    }
    try {
        $content = [System.IO.File]::ReadAllText($path)
    }
    catch {
        continue
    }
    if ($content -match $secretPattern) {
        $secretFiles += $relativePath
    }
}
if ($secretFiles.Count -gt 0) {
    $secretFiles | Sort-Object -Unique | ForEach-Object {
        Write-Host "possible secret: $_"
    }
    throw "Possible secret found; values were suppressed"
}

Invoke-Gate "imports" {
    & $python -B -c "import app.bot, app.api, app.service, app.catalog.db, app.catalog.discovery; print('imports_ok')"
}
Invoke-Gate "unit tests" {
    & $python -B -m pytest -p no:cacheprovider -q
}
Invoke-Gate "dependency consistency" {
    & $python -m pip check
}

if ($Live) {
    Invoke-Gate "live provider smoke" {
        & $python -B scripts/live_smoke.py
    }
}

Write-Host "All harness gates passed."
