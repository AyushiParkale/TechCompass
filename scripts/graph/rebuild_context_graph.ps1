param(
    [string]$GraphName = "anythingllm-context-v2-$(Get-Date -Format 'yyyyMMdd-HHmmss')",
    [switch]$ReuseExistingIndex
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $projectRoot
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = "python" }

$configuredGraph = (Select-String -Path (Join-Path $projectRoot ".env") `
    -Pattern '^\s*FALKORDB_GRAPH\s*=\s*(.+?)\s*$' -ErrorAction SilentlyContinue |
    Select-Object -First 1).Matches.Groups[1].Value.Trim('"', "'")
if ($configuredGraph -and $GraphName -eq $configuredGraph) {
    throw "Refusing to rebuild into the currently configured graph '$configuredGraph'. Supply a new graph name."
}
if ($GraphName -notmatch '^[A-Za-z0-9_-]{3,100}$') {
    throw "GraphName may contain only letters, numbers, underscores, and hyphens (3–100 characters)."
}

$graphIndexDirectory = Join-Path $projectRoot "data\index\$GraphName"
New-Item -ItemType Directory -Force $graphIndexDirectory | Out-Null
$env:FALKORDB_GRAPH = $GraphName
$env:TECHCOMPASS_CODEBASE_INDEX = "data/index/$GraphName/codebase.json"
$env:TECHCOMPASS_LOCKFILE_INDEX = "data/index/$GraphName/resolved_versions.json"

Write-Host "Building fresh AnythingLLM context graph: $GraphName"
Write-Host "The existing graph and its local index files will be preserved."

if ($ReuseExistingIndex) {
    $existingIndex = Join-Path $projectRoot "data\index\codebase.json"
    if (-not (Test-Path $existingIndex)) {
        throw "Cannot reuse the index; missing $existingIndex. Run without -ReuseExistingIndex."
    }
    Copy-Item -LiteralPath $existingIndex `
        -Destination (Join-Path $graphIndexDirectory "codebase.json") -Force
    Write-Host "Copied the existing source index into this graph's own index directory."
}

$buildArgs = @("-m", "scripts.graph.build")
if ($ReuseExistingIndex) { $buildArgs += "--reuse-existing-index" }
& $pythonExe @buildArgs
if ($LASTEXITCODE -ne 0) {
    throw "Graph build failed with exit code $LASTEXITCODE. The currently configured graph was not changed."
}
