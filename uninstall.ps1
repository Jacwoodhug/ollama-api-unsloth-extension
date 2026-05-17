$ROOT   = $PSScriptRoot
$INDEX  = "$ROOT\studio\unsloth_studio\Lib\site-packages\studio\frontend\dist\index.html"

# 1. Remove injected plugin tag from Studio WebUI
if (-not (Test-Path $INDEX)) {
    Write-Host "[UNINSTALL] index.html not found — skipping WebUI cleanup"
} elseif ((Get-Content $INDEX -Raw) -notmatch '11435/plugin\.js') {
    Write-Host "[UNINSTALL] Plugin tag not found in index.html — skipping"
} else {
    # Strip any line containing the plugin reference (handles both old src tag and new bootstrap)
    (Get-Content $INDEX) | Where-Object { $_ -notmatch '11435/plugin\.js' } |
        Set-Content $INDEX -Encoding utf8
    Write-Host "[UNINSTALL] Removed Ollama proxy plugin from Studio WebUI"
}

# 2. Uninstall proxy requirements from studio Python
$PYTHON = "$ROOT\studio\unsloth_studio\Scripts\python.exe"
$REQS   = "$ROOT\ollama-api\requirements.txt"
if ((Test-Path $PYTHON) -and (Test-Path $REQS)) {
    Write-Host "[UNINSTALL] Removing ollama-api dependencies..."
    & $PYTHON -m pip uninstall -y -r $REQS --quiet
    Write-Host "[UNINSTALL] Removed ollama-api dependencies"
}

# 3. Remove ollama-api folder
if (Test-Path "$ROOT\ollama-api") {
    Remove-Item "$ROOT\ollama-api" -Recurse -Force
    Write-Host "[UNINSTALL] Removed ollama-api/"
}

# 4. Remove launch scripts and the other uninstall script
foreach ($file in 'launch-unsloth.ps1', 'launch-unsloth.sh', 'uninstall.sh') {
    $path = "$ROOT\$file"
    if (Test-Path $path) {
        Remove-Item $path -Force
        Write-Host "[UNINSTALL] Removed $file"
    }
}

Write-Host "[UNINSTALL] Done."

# Self-delete
Remove-Item $MyInvocation.MyCommand.Path -Force
