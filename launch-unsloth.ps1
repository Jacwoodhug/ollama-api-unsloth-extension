$ROOT    = $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$PYTHON  = "$ROOT\studio\unsloth_studio\Scripts\python.exe"
$INDEX   = "$ROOT\studio\unsloth_studio\Lib\site-packages\studio\frontend\dist\index.html"
# Bootstrap that loads plugin.js from whichever host the user is browsing from,
# so it works for both localhost and Tailscale / remote access.
$PLUGIN_TAG = "<script>(function(){var s=document.createElement('script');s.src=location.protocol+'//'+location.hostname+':11435/plugin.js';s.defer=true;document.head.appendChild(s);})();</script>"
$OLD_PLUGIN_TAG = '<script src="http://localhost:11435/plugin.js" defer></script>'

# 1. Install proxy requirements into studio Python (once; skips if already satisfied)
$check = & $PYTHON -c "import fastapi, httpx, uvicorn, dotenv" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[SETUP] Installing ollama-api dependencies..."
    & $PYTHON -m pip install -r "$ROOT\ollama-api\requirements.txt" --quiet
}

# 2. Inject plugin script tag into Studio index.html (idempotent)
if (Test-Path $INDEX) {
    $content = Get-Content $INDEX -Raw
    if ($content.Contains($OLD_PLUGIN_TAG)) {
        # Migrate old hardcoded-localhost tag to the new dynamic bootstrap
        $content.Replace($OLD_PLUGIN_TAG, $PLUGIN_TAG) | Set-Content $INDEX -Encoding utf8
        Write-Host "[SETUP] Updated Ollama proxy plugin (dynamic hostname)"
    } elseif (-not $content.Contains('11435/plugin.js')) {
        $content.Replace('</body>', "$PLUGIN_TAG`n</body>") | Set-Content $INDEX -Encoding utf8
        Write-Host "[SETUP] Injected Ollama proxy plugin into Studio WebUI"
    }
}

# 3. Open browser after server starts (if enabled in settings)
$openBrowser = $false
$settingsFile = "$ROOT\ollama-api\settings.json"
if (Test-Path $settingsFile) {
    try { $openBrowser = (Get-Content $settingsFile -Raw | ConvertFrom-Json).open_browser_on_startup } catch {}
}
if ($openBrowser) {
    Start-Process powershell -WindowStyle Hidden -ArgumentList "-Command `"Start-Sleep 5; Start-Process 'http://127.0.0.1:8888'`""
}

# 4. Start manager (which auto-starts proxy) and unsloth as background jobs
$managerJob = Start-Job -ScriptBlock {
    param($root, $python)
    & $python "$root\ollama-api\manager.py" 2>&1
} -ArgumentList $ROOT, $PYTHON

$unslothJob = Start-Job -ScriptBlock {
    param($root)
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    & "$root\studio\unsloth_studio\Scripts\unsloth.exe" studio -p 8888 2>&1
} -ArgumentList $ROOT

# 5. Relay output: manager self-labels [PROXY] lines; PS1 adds [UNSLOTH] to unsloth
try {
    while ($true) {
        Receive-Job $managerJob  | ForEach-Object { Write-Host $_ }
        Receive-Job $unslothJob  | ForEach-Object { Write-Host "[UNSLOTH] $_" }
        if ($unslothJob.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 200
    }
} finally {
    Write-Host "[SETUP] Stopping manager and proxy..."
    $proxyPort = 11434
    $settingsFile = "$ROOT\ollama-api\settings.json"
    if (Test-Path $settingsFile) {
        try { $proxyPort = (Get-Content $settingsFile -Raw | ConvertFrom-Json).proxy_port } catch {}
    }
    foreach ($port in 8888, 11435, $proxyPort) {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }
    }
    Remove-Job $managerJob, $unslothJob -Force -ErrorAction SilentlyContinue
}
