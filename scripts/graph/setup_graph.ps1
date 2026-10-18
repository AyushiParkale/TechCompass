param(
    [string]$GraphName = "anythingllm-context-v2-$(Get-Date -Format 'yyyyMMdd-HHmmss')",
    [switch]$KeepCurrentGraph
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $projectRoot

function Invoke-Checked {
    param([string]$Description, [scriptblock]$Command)
    Write-Host "`n=== $Description ==="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Ensure-Repository {
    param([string]$Url, [string]$RelativePath)
    $target = Join-Path $projectRoot $RelativePath
    $sourceRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "data\source"))
    $targetFull = [IO.Path]::GetFullPath($target)
    if (-not $targetFull.StartsWith($sourceRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clone outside data/source: $RelativePath"
    }

    if (Test-Path -LiteralPath $targetFull) {
        if (-not (Test-Path -LiteralPath (Join-Path $targetFull ".git"))) {
            throw "Source directory already exists but is not a Git checkout: $targetFull. Move it aside or remove it, then rerun."
        }
        $origin = (& git -C $targetFull remote get-url origin 2>$null | Out-String).Trim()
        $normalizedOrigin = $origin.TrimEnd("/").Replace(".git", "")
        $normalizedUrl = $Url.TrimEnd("/").Replace(".git", "")
        if ($LASTEXITCODE -ne 0 -or $normalizedOrigin -ne $normalizedUrl) {
            throw "The existing checkout at $targetFull does not match $Url. It was left untouched."
        }
        Write-Host "Using existing checkout: $RelativePath"
        return
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $targetFull) | Out-Null
    Invoke-Checked "Clone $Url into $RelativePath" { git clone --depth 1 $Url $targetFull }
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required. Install Git for Windows, then rerun this script."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    throw "Create .env from .env.example and configure FalkorDB credentials before building the graph."
}
if ($GraphName -notmatch '^[A-Za-z0-9_-]{3,100}$') {
    throw "GraphName may contain only letters, numbers, underscores, and hyphens (3–100 characters)."
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked "Create Python virtual environment" { py -3 -m venv .venv }
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Invoke-Checked "Create Python virtual environment" { python -m venv .venv }
    } else {
        throw "Python 3.10 or newer is required. Install Python and rerun this script."
    }
}
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Could not find the virtual environment interpreter at $venvPython."
}

Ensure-Repository `
    -Url "https://github.com/Mintplex-Labs/anything-llm.git" `
    -RelativePath "data\source\anything-llm"
Ensure-Repository `
    -Url "https://github.com/Mintplex-Labs/anythingllm-docs.git" `
    -RelativePath "data\source\anythingllm-docs"

Invoke-Checked "Install TechCompass graph-ingestion dependencies" {
    & $venvPython -m pip install -r requirements.txt
}

$rebuildArgs = @{ GraphName = $GraphName }
Write-Host "`n=== Build a fresh FalkorDB graph: $GraphName ==="
& (Join-Path $projectRoot "scripts\graph\rebuild_context_graph.ps1") @rebuildArgs
if ($LASTEXITCODE -ne 0) {
    throw "Graph build failed. The currently configured graph was not changed."
}

if (-not $KeepCurrentGraph) {
    $envPath = Join-Path $projectRoot ".env"
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($line in [IO.File]::ReadAllLines($envPath)) { $lines.Add($line) }
    $graphLineFound = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match '^\s*FALKORDB_GRAPH\s*=') {
            $lines[$index] = "FALKORDB_GRAPH=$GraphName"
            $graphLineFound = $true
            break
        }
    }
    if (-not $graphLineFound) { $lines.Add("FALKORDB_GRAPH=$GraphName") }
    $content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($envPath, $content, $encoding)
    Write-Host "Activated graph '$GraphName' in .env. Restart the API to use it."
} else {
    Write-Host "Built '$GraphName'; kept the currently configured graph unchanged."
}

Write-Host "`nGraph setup complete. Check /health/ready after restarting the API."
