param([switch]$SkipFrontend, [switch]$SkipKnowledge)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$env:PYTHONUTF8 = '1'
$env:TEMP = Join-Path $projectRoot 'work\temp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $projectRoot 'work\cache\uv'
$env:npm_config_cache = Join-Path $projectRoot 'work\cache\npm'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$env:NO_PROXY = (@($env:NO_PROXY, '127.0.0.1', 'localhost') | Where-Object { $_ }) -join ','
$env:no_proxy = $env:NO_PROXY
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$runtime = Join-Path $projectRoot 'work\fullstack'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
function Test-LocalPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try { return ($client.ConnectAsync('127.0.0.1', $Port).Wait(500) -and $client.Connected) }
    catch { return $false }
    finally { $client.Dispose() }
}
$portable = Join-Path $runtime 'mysql-portable\mysql-8.4.13-winx64\bin\mysqld.exe'
$mysqlData = Join-Path $runtime 'mysql-local\data'
if ((Test-Path -LiteralPath $portable) -and (Test-Path -LiteralPath $mysqlData) -and -not (Test-LocalPort 33316)) {
    Start-Process -FilePath $portable -WindowStyle Hidden -PassThru -ArgumentList @(
        '--no-defaults', ('--datadir=' + $mysqlData), '--bind-address=127.0.0.1',
        '--port=33316', '--mysqlx=0', '--innodb-buffer-pool-size=128M', '--console'
    ) -RedirectStandardOutput (Join-Path $runtime 'mysql-local\server.stdout.log') `
      -RedirectStandardError (Join-Path $runtime 'mysql-local\server.stderr.log') | Out-Null
    for ($attempt=0; $attempt -lt 40 -and -not (Test-LocalPort 33316); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
}
& $python -m workbench.cli check
if ($LASTEXITCODE -ne 0) { throw 'Business database not ready. See docs/fullstack/README.md.' }
$ports = @(8082)
if (-not $SkipFrontend) { $ports += 5173 }
if (-not $SkipKnowledge) { $ports += @(8005,8083) }
foreach ($port in $ports) {
    if (Test-LocalPort $port) { throw "Port $port already in use; stop the existing checkout services first." }
}
$record = Join-Path $runtime 'processes.json'
$started = [System.Collections.Generic.List[object]]::new()
function Start-Component([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$Directory) {
    $process = Start-Process -FilePath $Executable -WindowStyle Hidden -PassThru -WorkingDirectory $Directory `
        -ArgumentList $Arguments -RedirectStandardOutput (Join-Path $runtime ($Name+'.stdout.log')) `
        -RedirectStandardError (Join-Path $runtime ($Name+'.stderr.log'))
    $started.Add(@{name=$Name; pid=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o')})
    @{root=$projectRoot; components=@($started.ToArray())} | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $record -Encoding utf8
}
function Wait-Component([string]$Url, [string]$Name) {
    for ($attempt=0; $attempt -lt 180; $attempt++) {
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2; return } catch {}
        $entry = $started | Where-Object { $_.name -eq $Name }
        if ($entry -and -not (Get-Process -Id $entry.pid -ErrorAction SilentlyContinue)) {
            throw "$Name exited; check work/fullstack/$Name.stderr.log."
        }
        Start-Sleep -Seconds 1
    }
    throw "$Name did not become ready; check work/fullstack/$Name.stderr.log."
}
try {
    if (-not $SkipKnowledge) {
        Start-Component 'chroma' $python @('-m','run_chroma','run','--host','127.0.0.1','--port','8005','--path','data/workbench/chroma-server') $projectRoot
        Wait-Component 'http://127.0.0.1:8005/api/v2/heartbeat' 'chroma'
        Start-Component 'scoped-mcp' $python @('-m','workbench.mcp_server') $projectRoot
        Wait-Component 'http://127.0.0.1:8083/health' 'scoped-mcp'
        Start-Component 'ingestion' $python @('-m','workbench.ingestion') $projectRoot
    }
    Start-Component 'api' $python @('-m','uvicorn','workbench.app:app','--host','127.0.0.1','--port','8082') $projectRoot
    Wait-Component 'http://127.0.0.1:8082/ready' 'api'
    if (-not $SkipFrontend) {
        $vite = Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js'
        if (-not (Test-Path -LiteralPath $vite)) { throw 'Run npm --prefix frontend ci first.' }
        Start-Component 'frontend' (Get-Command node).Source @($vite,'--host','127.0.0.1') (Join-Path $projectRoot 'frontend')
    }
} catch {
    & (Join-Path $PSScriptRoot 'stop-workbench.ps1')
    throw
}
Write-Output 'API: http://127.0.0.1:8082/docs'
if (-not $SkipFrontend) { Write-Output 'Workbench: http://127.0.0.1:5173' }
Write-Output 'Logs: work/fullstack/*.stderr.log; Stop: powershell -File scripts/stop-workbench.ps1'
