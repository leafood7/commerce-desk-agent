param(
    [ValidateSet('Config','Build','Start','Test','Stop')]
    [string]$Phase='Config'
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
$docker='D:\Docker\app\resources\bin\docker.exe'
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
$env:PYTHONUTF8='1'
# Go-based registry authentication does not automatically use Windows Internet Settings.
if (-not $env:HTTPS_PROXY) {
    $proxy=Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($proxy.ProxyEnable -eq 1 -and $proxy.ProxyServer -match '^127\.0\.0\.1:\d+$') {
        $env:HTTP_PROXY='http://'+$proxy.ProxyServer
        $env:HTTPS_PROXY=$env:HTTP_PROXY
        $env:NO_PROXY='localhost,127.0.0.1,*.docker.internal'
    }
}

$env:TEMP='D:\Docker\temp'
$env:TMP=$env:TEMP
$env:DOCKER_CONFIG='D:\Docker\config'
$env:UV_CACHE_DIR='D:\Docker\cache\uv'
$env:npm_config_cache=Join-Path $projectRoot 'work\cache\npm'
$runtime=Join-Path $projectRoot 'work\docker'
$environment=Join-Path $runtime 'acceptance.env'
if (-not (Test-Path -LiteralPath $environment)) { throw 'Private work/docker/acceptance.env is missing; do not use real business credentials.' }
if (-not (Test-Path -LiteralPath $docker)) { throw 'Docker Desktop is not installed at the expected D-drive path.' }
New-Item -ItemType Directory -Force -Path $env:DOCKER_CONFIG,$env:TEMP | Out-Null
$compose=@('--host','npipe:////./pipe/dockerDesktopLinuxEngine','compose',
    '--project-name','commercedesk-acceptance','--env-file',$environment,
    '-f','compose.yaml','-f','compose.acceptance.yaml')
function Invoke-Compose([string[]]$Arguments) {
    & $docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Docker Compose failed in phase '+$Phase) }
}
function Require-Engine {
    if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
        throw 'Restart Windows after enabling VirtualMachinePlatform, then continue Docker acceptance.'
    }
    & $docker --host npipe:////./pipe/dockerDesktopLinuxEngine info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop and wait for the Linux engine, then rerun this phase.' }
    $disks=@(Get-ChildItem -LiteralPath 'D:\Docker\data' -Filter '*.vhdx' -Recurse -ErrorAction SilentlyContinue)
    if (-not $disks.Count) { throw 'No Docker data disk verified on D drive. Fix disk location before pulling images.' }
}
function Seed-Orders {
    Invoke-Compose @('exec','-T','api','python','scripts/seed_aftersales_demo.py') | Out-Host
    $json=& $docker @compose exec -T api cat work/fullstack/aftersales-demo.json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read isolated container demo order IDs.' }
    return ($json -join [Environment]::NewLine) | ConvertFrom-Json
}
function Seed-Returns {
    $json=& $docker @compose exec -T api python scripts/seed_return_demo.py
    if ($LASTEXITCODE -ne 0) { throw 'Return demo preparation failed; original orders retained.' }
    $target=Join-Path $projectRoot 'work\returns-v1\demo.json'
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    [IO.File]::WriteAllText($target,($json -join [Environment]::NewLine),[Text.UTF8Encoding]::new($false))
}
Invoke-Compose @('config','--quiet')
switch ($Phase) {
    'Config' {
        Invoke-Compose @('config','--services')
        Write-Output 'Configuration parsed only; no container runtime success claimed.'
    }
    'Build' {
        Require-Engine
        Invoke-Compose @('build')
    }
    'Start' {
        Require-Engine
        Invoke-Compose @('up','-d','--wait','--wait-timeout','300')
        Invoke-Compose @('exec','-T','api','python','-m','workbench.cli','seed')
        Invoke-Compose @('ps')
        Write-Output 'Acceptance API http://127.0.0.1:18082; web http://127.0.0.1:15173'
    }
    'Test' {
        Require-Engine
        $values=@{}
        foreach ($line in (Get-Content -LiteralPath $environment)) {
            if ($line -match '^([^#=]+)=(.*)$') { $values[$matches[1]]=$matches[2] }
        }
        $env:WORKBENCH_TEST_PASSWORD=$values['WORKBENCH_DOCKER_DEMO_PASSWORD']
        $env:WORKBENCH_TEST_URL='http://127.0.0.1:18082'
        $env:WORKBENCH_WEB_URL='http://127.0.0.1:15173'
        $env:USE_FAKE_MODEL='true'
        $env:HF_HUB_OFFLINE='1'
        $env:PYTHONPATH=Join-Path $projectRoot 'src'
        $orders=Seed-Orders
        $env:WORKBENCH_DOCKER_ORDER_NUMBER=$orders.expedite
        Seed-Returns
        Copy-Item -LiteralPath (Join-Path $projectRoot 'work\returns-v1\demo.json') -Destination (Join-Path $projectRoot 'work\returns-v1\demo-api.json') -Force
        & $python -m pytest --run-docker tests/integration/test_workbench_docker.py tests/integration/test_workbench_docker_flows.py tests/integration/test_workbench_dispatch_docker.py tests/integration/test_workbench_profiles_docker.py tests/integration/test_workbench_returns_docker.py tests/integration/test_workbench_return_agent_docker.py -q `
            --basetemp=work/temp/docker-api --junitxml=work/docker/api-junit.xml
        if ($LASTEXITCODE -ne 0) { throw 'Real container API acceptance failed; preserve logs.' }
        $hostManifest=Join-Path $projectRoot 'work\fullstack\aftersales-demo.json'
        $backupManifest=Join-Path $runtime 'host-aftersales-demo.backup.json'
        $hadManifest=Test-Path -LiteralPath $hostManifest
        if ($hadManifest) { Copy-Item -LiteralPath $hostManifest -Destination $backupManifest -Force }
        try {
            $orders=Seed-Orders
            Seed-Returns
            [IO.File]::WriteAllText($hostManifest, ($orders | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
            npm --prefix frontend run test:e2e
            if ($LASTEXITCODE -ne 0) { throw 'Container browser acceptance failed; preserve artifacts.' }
        } finally {
            if ($hadManifest) {
                Copy-Item -LiteralPath $backupManifest -Destination $hostManifest -Force
            } else {
                Remove-Item -LiteralPath $hostManifest -ErrorAction SilentlyContinue
            }
        }
        Invoke-Compose @('ps','--format','json') | Set-Content -LiteralPath (Join-Path $runtime 'containers.jsonl') -Encoding utf8
        Write-Output 'API and browser flows passed. Persistence/restart and fault recovery remain separate checks.'
    }
    'Stop' {
        Require-Engine
        Invoke-Compose @('stop')
        Write-Output 'Acceptance containers stopped; persistent volumes retained.'
    }
}
