$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$runtimeDir = Join-Path $projectRoot 'work'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$env:TEMP = Join-Path $runtimeDir 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Test-LocalPort([int]$port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1', $port)
        return ($task.Wait(1000) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

if ((Test-LocalPort 8081) -or (Test-LocalPort 8501)) {
    throw 'Port 8081 or 8501 is already in use. Reuse the running service or stop it first.'
}

& $python scripts/init_demo_orders.py
if ($LASTEXITCODE -ne 0) {
    throw 'Demo order database initialization failed.'
}

& $python scripts/init_knowledge.py --if-missing
if ($LASTEXITCODE -ne 0) {
    throw 'Policy index initialization failed. See LOCAL_SUPPORTPILOT.md.'
}

& $python scripts/init_reranker.py --if-enabled
if ($LASTEXITCODE -ne 0) {
    throw 'Reranker initialization failed. See docs/rag-optimization.md.'
}

$service = Start-Process -FilePath $python -ArgumentList @('src/run_service.py') `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'service.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'service.stderr.log')
try {
    $web = Start-Process -FilePath $python -ArgumentList @(
        '-m', 'streamlit', 'run', 'src/streamlit_app.py',
        '--server.address', '127.0.0.1', '--server.port', '8501',
        '--server.headless', 'true', '--browser.gatherUsageStats', 'false'
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir 'web.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'web.stderr.log')
} catch {
    Stop-Process -Id $service.Id -ErrorAction SilentlyContinue
    throw
}
@{ service = $service.Id; web = $web.Id } | ConvertTo-Json | Set-Content `
    -LiteralPath (Join-Path $runtimeDir 'local-processes.json')
Write-Output 'Starting API: http://127.0.0.1:8081/docs'
Write-Output 'Starting web: http://127.0.0.1:8501'
Write-Output 'Logs: work/service.stderr.log and work/web.stderr.log'
Write-Output 'Keep this terminal open. Press Ctrl+C to stop both services.'
try {
    Wait-Process -Id $service.Id, $web.Id
} finally {
    Stop-Process -Id $service.Id, $web.Id -ErrorAction SilentlyContinue
}
